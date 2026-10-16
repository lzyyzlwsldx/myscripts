import os
import re
import csv
import json
import sys
import shutil
import logging
import tempfile
import argparse
import subprocess
from functools import partial
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Set, Any
from pathlib import Path
import yaml

CONTROL_COLUMNS_MAP = {
    'var':
        [
            ['序号 变量键（KEY） 变量描述 变量类型 填写示例 文件路径 填写说明', 1.0]
        ],
    'deploy':
        [
            ['步骤 资源类型 资源名称 命名空间 部署类型 YAML路径 镜像包名称 备注', 1.0],
            ['步骤 资源类型 资源名称 命名空间 部署类型 YAML路径 镜像引用digest 备注', 1.1],
        ],
    'script': [
        ['步骤 脚本路径 是否幂等 是否依赖 执行机类型 执行用户 K8S命名空间 负载资源名称 备注', 1.0]
    ]
}
# deploy-execution-plan.csv columns
DEPLOY_RESOURCE_TYPE = {'namespace': 0,
                        'sa': 1.0, 'serviceaccount': 1.0, 'role': 1.0, 'cr': 1.0, 'clusterrole': 1.0,
                        'rolebinding': 1.1, 'crb': 1.1, 'clusterrolebinding': 1.1,
                        'configmap': 1, 'secret': 1, 'pvc': 1, 'persistentvolumeclaim': 1,
                        'deployment': 2, 'statefulset': 2, 'daemonset': 2, 'job': 2, 'cronjob': 2, 'service': 2,
                        'hpa': 3, 'vpa': 3,
                        'ingress': 4}
K8S_RESOURCE_SUPPORT = {'Namespace',
                        'SA or ServiceAccount', 'Role', 'CR or ClusterRole',
                        'RoleBinding', 'CRB or ClusterRoleBinding',
                        'ConfigMap', 'Secret', 'PVC or PersistentVolumeClaim',
                        'Deployment', 'StatefulSet', 'DaemonSet', 'Job', 'CronJob', 'Service',
                        'HPA', 'VPA',
                        'Ingress'}
K8S_ABBR = [{'sa', 'serviceaccount'},
            {'cr', 'clusterrole'}, {'crb', 'clusterrolebinding'},
            {'pvc', 'persistentvolumeclaim'}, ]
DEPLOY_NEED_IMAGE = {'deployment', 'statefulset', 'daemonset', 'job', 'cronjob'}
DEPLOY_TYPE = {'更新', '下线', '重启', '镜像拉取'}
# global-vars.csv columns
VAR_TYPE = {'字符串', '数值', '布尔'}
CONTROL_TYPE_MAP = {'var': 'global-vars', 'deploy': 'deploy-execution-plan', 'script': 'script-execution-plan'}
# 两种成对匹配：\x02…\x03 或 \\x02…\\x03，最长64个
PATTERN_MAP = {'字符串和控制符': re.compile(br'\x02(.{1,64}?)\x03|\\x02(.{1,64}?)\\x03', re.DOTALL),
               '仅控制符': re.compile(br'\x02(.{1,64}?)\x03', re.DOTALL)}
START_TOKENS_MAP: Dict[str, Tuple[bytes, ...]] = {'字符串和控制符': (b'\x02', b'\\x02'), '仅控制符': (b'\x02',), }
DEFAULT_IMAGE_PREFIX = 'swr.cn-north-4.myhuaweicloud.com/sl-shudi-pre_release'
DEFAULT_SQL_MAX_PARSE_NODES = 100_000
DEFAULT_SQL_CHUNK_CHARS = 64 * 1024
SQL_NON_CODE_RE = re.compile(
    r"/\*[\s\S]*?\*/|--[^\n]*|(?<!\S)\#[^\n]*|'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"",
    re.MULTILINE,
)
MYSQL_TRANSACTION_BEGIN_RE = re.compile(
    r'^[ \t]*(BEGIN(?:[ \t]+WORK)?)[ \t]*;[ \t]*\r?$',
    re.IGNORECASE | re.MULTILINE,
)
SQL_DOLLAR_QUOTE_RE = re.compile(r'\$[A-Za-z_][A-Za-z_0-9]*\$|\$\$')
MYSQL_DELIMITER_LINE_RE = re.compile(r'[ \t]*DELIMITER[ \t]+(\S+)[ \t]*(?:\r?\n|$)', re.IGNORECASE)
SQL_WORD_RE = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
ORACLE_SLASH_LINE_RE = re.compile(r'[ \t]*/[ \t]*(?:\r?\n|$)')
TSQL_TRANSACTION_RE = re.compile(r'\s+TRAN(?:SACTION)?\b', re.IGNORECASE)
SQL_END_CLAUSE_RE = re.compile(r'\s+(?:IF|LOOP|CASE)\b', re.IGNORECASE)
SQL_DIALECT_PATTERNS = {
    'mysql': r'\bAUTO_INCREMENT\b|\bENGINE\s*=|\bDELIMITER\b|\bON\s+DUPLICATE\s+KEY\b|\bINSERT\s+IGNORE\b|\bFOREIGN_KEY_CHECKS\b|\bINTO\s+@[A-Za-z_]\w*',
    'postgres': r'::\s*[A-Za-z_]|\bILIKE\b|\bON\s+CONFLICT\b|\bLANGUAGE\s+plpgsql\b|\bCREATE\s+EXTENSION\b|\bDISTINCT\s+ON\s*\(|\bSET\s+search_path\b',
    'oracle': r'\bVARCHAR2\b|\bNVARCHAR2\b|\bCONNECT\s+BY\b|\bSYSTIMESTAMP\b|\bDBMS_[A-Z_]+\b|\bEXECUTE\s+IMMEDIATE\b',
    'tsql': r'^\s*GO\s*(?:\d+)?$|\bIDENTITY\s*\(\s*\d+\s*,\s*\d+\s*\)|\bWITH\s*\(\s*NOLOCK\s*\)|\bTOP\s*\(?\s*\d+',
    'sqlite': r'\bPRAGMA\b|\bAUTOINCREMENT\b|\bWITHOUT\s+ROWID\b|\bINSERT\s+OR\s+(?:IGNORE|REPLACE)\b|\bsqlite_master\b',
}


class ErrorType(object):
    SUCCESS = 0
    VALUE_NOT_EXIST = 1  # 值不存在
    DEPLOY_RESOURCE_TYPE_ERROR = 2  # 部署资源类型错误
    DEPLOY_TYPE_ERROR = 3  # 部署类型错误
    DEPLOY_ORDER_ERROR = 4  # 部署顺序错误
    DEPLOY_YAML_NOT_EXIST = 5  # k8s Yaml不存在
    DEPLOY_YAML_NAME_ERROR = 6  # 不符合命名规范
    VAR_TYPE_ERROR = 7  # 变量类型错误
    PATH_NOT_EXIST = 8  # 路径不存在
    BOOL_ERROR = 9  # bool值类型错误
    SCRIPT_ENV_TYPE_ERROR = 10  # 脚本执行环境类型错误
    DEPLOY_IMG_REF_ERROR = 11  # 镜像引用不符合规范
    DEPLOY_METADATA_NAME_ERROR = 12  # 资源名称与 YAML metadata.name 不一致
    DEPLOY_CONTAINER_NAME_ERROR = 13  # 资源名称与 YAML containers.name 不一致
    DEPLOY_HEALTH_PROBE_ERROR = 14  # Deployment 主容器健康检查探针缺失
    CSV_EXTRA_COLUMNS_ERROR = 15  # CSV 数据行包含表头之外的字段
    PLAN_STEP_FORMAT_ERROR = 16  # 执行计划步骤不是整数
    PLAN_STEP_DUPLICATE_ERROR = 17  # 同一执行计划内步骤重复
    PLAN_STEP_ORDER_ERROR = 18  # 执行计划步骤未按数值递增
    DEPLOY_UNNEEDED_IMAGE_ERROR = 19  # 不需要镜像的资源填写了镜像引用 digest
    DEPLOY_YAML_KIND_ERROR = 20  # YAML kind 与执行计划资源类型不一致
    DEPLOY_NAMESPACE_ERROR = 21  # YAML metadata.namespace 与执行计划命名空间不一致


ERROR_LOG_MAP = {
    ErrorType.SUCCESS: '成功',
    ErrorType.VALUE_NOT_EXIST: '值不存在，请处理！',
    ErrorType.DEPLOY_RESOURCE_TYPE_ERROR: f'不在支持资源类型范围内，请调整！支持的资源类型为 {K8S_RESOURCE_SUPPORT}',
    ErrorType.DEPLOY_TYPE_ERROR: '不在支持部署类型范围内，请调整！',
    ErrorType.DEPLOY_ORDER_ERROR: '部署先后顺序有误，请根据资源类型调整部署顺序！',
    ErrorType.DEPLOY_YAML_NOT_EXIST: '相对路径不存在，请调整！',
    ErrorType.DEPLOY_YAML_NAME_ERROR: '不符合 YAML 命名规范，请调整后缀！',
    ErrorType.VAR_TYPE_ERROR: '不在支持变量类型范围内，请调整！',
    ErrorType.PATH_NOT_EXIST: '相对路径不存在，如多个文件路径则使用换行分隔，请调整！',
    ErrorType.BOOL_ERROR: '请填写是或否，如需补充说明请写至备注中。',
    ErrorType.SCRIPT_ENV_TYPE_ERROR: '请填写宿主机或容器，如需补充说明请写至备注中。',
    ErrorType.DEPLOY_IMG_REF_ERROR: '镜像引用不符合规范，请调整！',
    ErrorType.DEPLOY_METADATA_NAME_ERROR: '与对应 YAML metadata.name【{}】不一致，请调整！',
    ErrorType.DEPLOY_CONTAINER_NAME_ERROR: '与对应 YAML containers.name【{}】不一致，请调整！',
    ErrorType.DEPLOY_HEALTH_PROBE_ERROR: '对应 YAML 主容器缺少健康检查探针【{}】，请补充！',
    ErrorType.CSV_EXTRA_COLUMNS_ERROR: (
        '数据行有 {} 列，表头仅有 {} 列，多余字段为【{}】。'
        '如字段内容包含英文逗号，请使用双引号包裹！'
    ),
    ErrorType.PLAN_STEP_FORMAT_ERROR: '步骤序号请填写整数！',
    ErrorType.PLAN_STEP_DUPLICATE_ERROR: '步骤序号与第 {} 行重复，同一执行计划内不允许重复！',
    ErrorType.PLAN_STEP_ORDER_ERROR: '步骤序号应按数值递增排列，允许间隔，请调整！',
    ErrorType.DEPLOY_UNNEEDED_IMAGE_ERROR: '该资源类型不需要镜像，镜像引用digest 列应留空！',
    ErrorType.DEPLOY_YAML_KIND_ERROR: '对应 YAML 未找到执行计划资源类型的 kind，请调整！',
    ErrorType.DEPLOY_NAMESPACE_ERROR: '与对应 YAML metadata.namespace【{}】不一致，请调整！',
}


class CheckError(object):
    def __init__(self, error_type, error_values=()):
        self.error_type = error_type
        self.error_values = error_values
        error_msg = ERROR_LOG_MAP[error_type]
        self.error_msg = error_msg.format(*error_values) if error_values else error_msg


@dataclass
class DeployValidationContext:
    install_path: str
    version: float
    image_column: str
    global_var_values: Dict[str, str]
    yaml_documents_cache: Dict[str, Tuple[List[Any], Dict[str, str]]] = field(default_factory=dict)
    sequence_last: float = 0


def map_row_by_columns(column_names, row):
    """将 CSV 行映射为列名字典；缺失的尾部字段按空字符串处理。"""
    values = list(row[:len(column_names)])
    values += [''] * (len(column_names) - len(values))
    return dict(zip(column_names, values))


def is_empty_csv_row(row):
    return all(cell is None or str(cell).strip() == '' for cell in row)


def normalize_csv_rows_for_display(rows):
    """过滤空白数据行，并将其余行补齐或截断到表头列数。"""
    if not rows or is_empty_csv_row(rows[0]):
        return [], [], []

    column_names = list(rows[0])
    normalized_rows, source_indices = [], []
    for index, row in enumerate(rows[1:]):
        if is_empty_csv_row(row):
            continue
        values = list(row[:len(column_names)])
        values += [''] * (len(column_names) - len(values))
        normalized_rows.append(values)
        source_indices.append(index)
    return column_names, normalized_rows, source_indices


def check_k8s_kind_same(n1, n2):
    """检查的两个类型名字是否相同"""
    n1_lower, n2_lower = n1.lower(), n2.lower()
    for i in K8S_ABBR:
        if n1_lower in i and n2_lower in i:
            return True
    return n1_lower == n2_lower


def load_global_var_values(install_path):
    """读取全局变量填写示例，供 YAML 名称占位符解析使用。"""
    global_vars_path = os.path.join(install_path, 'controls/global-vars.csv')
    if not os.path.exists(global_vars_path):
        return {}
    line_data = read_controls_csv(global_vars_path)
    column_names = CONTROL_COLUMNS_MAP['var'][0][0].split(' ')
    return {
        row['变量键（KEY）']: row['填写示例']
        for row in (map_row_by_columns(column_names, values) for values in line_data[1:])
        if row['变量键（KEY）']
    }


def tokenize_yaml_placeholders(yaml_path):
    """
    将 YAML 中的占位符替换成可安全解析的临时 token。

    不直接写入全局变量值，避免变量中的 YAML 特殊字符影响结构解析；
    名称取出后再使用全局变量值还原。
    """
    content = Path(yaml_path).read_bytes()
    key_token_map, token_key_map = {}, {}

    def _replacer(match):
        key = decode_key(match.group(1) or match.group(2))
        if key not in key_token_map:
            token_index = len(key_token_map)
            token = f'__INSTALL_AGENT_PLACEHOLDER_{token_index}__'
            while token.encode() in content or token in token_key_map:
                token_index += 1
                token = f'__INSTALL_AGENT_PLACEHOLDER_{token_index}__'
            key_token_map[key] = token
            token_key_map[token] = key
        return key_token_map[key].encode()

    tokenized_content = PATTERN_MAP['字符串和控制符'].sub(_replacer, content)
    documents = list(yaml.safe_load_all(tokenized_content.decode('utf-8-sig')))
    return documents, token_key_map


def resolve_template_text(value, variables):
    """替换字符串中的占位符，并返回未定义的变量键。"""
    if value is None:
        return None, set()
    missing_keys = set()

    def _replacer(match):
        key = decode_key(match.group(1) or match.group(2))
        if key not in variables:
            missing_keys.add(key)
            return match.group(0)
        return str(variables[key]).encode()

    resolved = PATTERN_MAP['字符串和控制符'].sub(_replacer, str(value).encode())
    return resolved.decode(), missing_keys


def resolve_yaml_name(value, token_key_map, variables):
    """还原从 token 化 YAML 中读取出的名称。"""
    if value is None:
        return None, set()
    resolved, missing_keys = str(value), set()
    for token, key in token_key_map.items():
        if token not in resolved:
            continue
        if key not in variables:
            missing_keys.add(key)
            continue
        resolved = resolved.replace(token, str(variables[key]))
    return resolved, missing_keys


def get_workload_containers(document, resource_type):
    """获取工作负载中的 containers，兼容 CronJob 的 jobTemplate 层级。"""
    spec = document.get('spec')
    if not isinstance(spec, dict):
        return []
    if resource_type.lower() == 'cronjob':
        job_template = spec.get('jobTemplate')
        spec = job_template.get('spec') if isinstance(job_template, dict) else None
        if not isinstance(spec, dict):
            return []
    template = spec.get('template')
    pod_spec = template.get('spec') if isinstance(template, dict) else None
    return pod_spec.get('containers', []) if isinstance(pod_spec, dict) else []


def resolve_deploy_resource(resource_type, resource_name, documents, token_key_map, variables):
    """按资源类型和名称定位执行计划对应的 YAML 文档。"""
    expected_name, missing_keys = resolve_template_text(resource_name, variables)
    if not expected_name or missing_keys:
        return None, None, [], True

    same_kind_documents = [
        document for document in documents
        if isinstance(document, dict)
           and isinstance(document.get('kind'), str)
           and check_k8s_kind_same(resource_type, document['kind'])
    ]
    resolved_documents, unresolved_metadata = [], False
    for document in same_kind_documents:
        metadata = document.get('metadata')
        metadata_name = metadata.get('name') if isinstance(metadata, dict) else None
        resolved_name, unresolved = resolve_yaml_name(metadata_name, token_key_map, variables)
        unresolved_metadata = unresolved_metadata or bool(unresolved)
        resolved_documents.append((document, resolved_name))

    target_document = next(
        (document for document, metadata_name in resolved_documents if metadata_name == expected_name),
        None,
    )
    actual_names = [name if name is not None else '<未填写>' for _, name in resolved_documents]
    return expected_name, target_document, actual_names, unresolved_metadata


def find_named_container(document, resource_type, expected_name, token_key_map, variables):
    """查找名称与执行计划资源名称一致的主容器。"""
    containers = get_workload_containers(document, resource_type)
    if not isinstance(containers, list):
        return None, [], False, False

    container_names, unresolved_container, has_container_name = [], False, False
    for container in containers:
        container_name = container.get('name') if isinstance(container, dict) else None
        has_container_name = has_container_name or container_name not in (None, '')
        resolved_name, unresolved = resolve_yaml_name(container_name, token_key_map, variables)
        unresolved_container = unresolved_container or bool(unresolved)
        container_names.append(resolved_name if resolved_name is not None else '<未填写>')
        if not unresolved and resolved_name == expected_name:
            return container, container_names, unresolved_container, has_container_name
    return None, container_names, unresolved_container, has_container_name


def check_deploy_resource_names(resource_type, resource_name, documents, token_key_map, variables,
                               check_containers=True):
    """检查执行计划资源名称与 YAML metadata.name、containers.name 是否一致。"""
    expected_name, target_document, actual_names, unresolved_metadata = resolve_deploy_resource(
        resource_type, resource_name, documents, token_key_map, variables,
    )
    if expected_name is None:
        return []
    if target_document is None:
        if unresolved_metadata:
            return []
        return [CheckError(ErrorType.DEPLOY_METADATA_NAME_ERROR, (', '.join(actual_names) or '<未找到>',))]

    if not check_containers or resource_type.lower() not in DEPLOY_NEED_IMAGE:
        return []

    target_container, container_names, unresolved_container, has_container_name = find_named_container(
        target_document, resource_type, expected_name, token_key_map, variables,
    )
    if target_container is not None or unresolved_container:
        return []
    if resource_type.lower() == 'deployment' and not has_container_name:
        return []
    return [CheckError(
        ErrorType.DEPLOY_CONTAINER_NAME_ERROR,
        (', '.join(container_names) or '<未填写>',),
    )]


def check_deployment_health_probes(resource_type, resource_name, documents, token_key_map, variables):
    """检查 Deployment 主容器是否配置启动和存活探针。"""
    if resource_type.lower() != 'deployment':
        return []

    expected_name, target_document, _, _ = resolve_deploy_resource(
        resource_type, resource_name, documents, token_key_map, variables,
    )
    if expected_name is None or target_document is None:
        return []

    target_container, _, _, _ = find_named_container(
        target_document, resource_type, expected_name, token_key_map, variables,
    )
    if target_container is None:
        return []

    missing_probes = []
    for probe_name in ('startupProbe', 'livenessProbe'):
        probe = target_container.get(probe_name)
        if not isinstance(probe, dict) or not probe:
            missing_probes.append(probe_name)
    if not missing_probes:
        return []
    return [CheckError(ErrorType.DEPLOY_HEALTH_PROBE_ERROR, (', '.join(missing_probes),))]


def check_standard(install_dir, *, strict=True):
    assert os.path.exists(install_dir), '{} 部署包目录不存在，请输入正确路径！'.format(install_dir)
    assert os.path.exists(os.path.join(install_dir, 'controls/global-vars.csv')), '未发现【全局变量清单】global-vars.csv，请确认！！！'
    deploy_csv_path = os.path.join(install_dir, 'controls/deploy-execution-plan.csv')
    assert os.path.exists(deploy_csv_path), '未发现【k8s编排计划】deploy-execution-vars.csv，请确认！！！'
    _, _, deploy_error_ret, _ = load_data_from_csv(deploy_csv_path, 'deploy', strict=strict)
    script_csv_path = os.path.join(install_dir, 'controls/script-execution-plan.csv')
    assert os.path.exists(script_csv_path), '未发现【script编排计划】script-execution-vars.csv，请确认！！！'
    _, _, script_error_ret, _ = load_data_from_csv(script_csv_path, 'script', strict=strict)
    return deploy_error_ret, script_error_ret


def query_template_paths(root_dir):
    """
    查找全部模版文件路径
    :param root_dir:
    :return:
    """
    paths = set()

    def walk_files(template_dir):
        for subdir, _, files in os.walk(template_dir):
            for filename in files:
                filepath = os.path.join(subdir, filename)
                os.path.basename(filepath) != 'global-vars.csv' and paths.add(filepath)

    walk_files(os.path.join(root_dir, 'k8s-resources'))
    walk_files(os.path.join(root_dir, 'scripts'))
    walk_files(os.path.join(root_dir, 'controls'))
    return paths


def deploy_compare_lte(a, b):
    if type(a) != int and type(b) != int:
        return a <= b
    return int(a) <= int(b)


def generate_csv_logs(error_mask, line_data):
    error_logs = []
    cols, data = line_data[0], line_data[1:]

    def error_pos_log(row_index, column_index):
        row = data[row_index]
        value = row[column_index] if column_index < len(row) else ''
        return '第 {} 行 {} 列，【{}】 {}'.format(
            row_index + 1, column_index + 1, cols[column_index], value,
        )

    for ri in range(len(error_mask)):
        for ci in range(len(error_mask[ri])):
            error_list = error_mask[ri][ci]
            error_logs.extend([f'{error_pos_log(ri, ci)} {v.error_msg}' for v in error_list])
            error_mask[ri][ci] = error_list and error_list[0] or CheckError(ErrorType.SUCCESS)
    return error_logs


def check_ref_digest(ref_digest):
    if ref_digest.count('@') != 1:
        return False
    ref_repo, digest = ref_digest.split('@', 1)
    if not ref_repo or not digest:
        return False
    if len(ref_repo.split('/')) < 4:
        return False
    return True


def update_images(obj, image_ref):
    """
    递归遍历 YAML 对象，只替换 containers / initContainers / ephemeralContainers 中的 image。
    """
    if not isinstance(obj, (dict, list)):
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            # 找到容器列表字段
            if key in ("containers", "initContainers", "ephemeralContainers") and isinstance(value, list):
                for c in value:
                    if isinstance(c, dict) and "image" in c:
                        c["image"] = image_ref
            else:
                update_images(value, image_ref)
    else:
        for item in obj:
            update_images(item, image_ref)


def update_image_in_yaml(yaml_path, image_ref):
    """
    读取 YAML 文件，更新镜像前缀，并写回原文件。
    支持多文档 YAML（--- 分隔）。
    """
    path = Path(yaml_path)
    with path.open("r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))
    for doc in docs:
        update_images(doc, image_ref)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump_all(docs, f, sort_keys=False, allow_unicode=True)


def replace_k8s_images(install_dir, old_prefix, new_prefix):
    if not new_prefix or not old_prefix or old_prefix == new_prefix: return
    line_data = read_controls_csv(os.path.join(install_dir, 'controls/deploy-execution-plan.csv'))
    if not line_data:
        return
    column_names = line_data[0]
    image_column = '镜像引用digest' if '镜像引用digest' in column_names else '镜像包名称'
    for values in line_data[1:]:
        if is_empty_csv_row(values):
            continue
        row = map_row_by_columns(column_names, values)
        yaml_path, image = row.get('YAML路径', ''), row.get(image_column, '')
        if not yaml_path or not image:
            continue
        k8s_yaml_path = os.path.join(install_dir, yaml_path)
        if not os.path.exists(k8s_yaml_path): continue
        image_ref = image.replace(old_prefix, new_prefix, 1)
        update_image_in_yaml(k8s_yaml_path, image_ref)


def get_control_csv_schema(line_data, control_type) -> tuple[list[str], float]:
    """校验中控 CSV 表头并返回列定义和规范版本。"""
    control_name = CONTROL_TYPE_MAP[control_type]
    if not line_data:
        raise AssertionError('{} 文件格式有误，未发现列名！'.format(control_name))
    for column_string, version in CONTROL_COLUMNS_MAP[control_type]:
        column_names = column_string.split(' ')
        if line_data[0] == column_names:
            return column_names, float(version)
    raise AssertionError('{} 文件列名不匹配！列名应为 {}'.format(
        control_name, CONTROL_COLUMNS_MAP[control_type][-1],
    ))


def append_required_value_errors(row, row_errors, column_names):
    for column_name in column_names:
        if not row[column_name]:
            row_errors[column_name].append(CheckError(ErrorType.VALUE_NOT_EXIST))


def validate_var_row(row, row_errors):
    append_required_value_errors(
        row,
        row_errors,
        ['序号', '变量键（KEY）', '变量描述', '变量类型', '填写说明'],
    )
    if row['变量类型'] not in VAR_TYPE:
        row_errors['变量类型'].append(CheckError(ErrorType.VAR_TYPE_ERROR))


def validate_plan_step(row, row_errors, seen_steps, row_number):
    """按文件检查步骤整数、唯一性和递增顺序，不要求连续或从 1 开始。"""
    value = row['步骤']
    if not value:
        # 空值由各执行计划的必填项检查处理。
        return
    try:
        step = int(value)
    except ValueError:
        row_errors['步骤'].append(CheckError(ErrorType.PLAN_STEP_FORMAT_ERROR))
        return
    if step in seen_steps:
        row_errors['步骤'].append(CheckError(
            ErrorType.PLAN_STEP_DUPLICATE_ERROR, (seen_steps[step],),
        ))
        return
    if seen_steps and step < next(reversed(seen_steps)):
        row_errors['步骤'].append(CheckError(ErrorType.PLAN_STEP_ORDER_ERROR))
    seen_steps[step] = row_number


def validate_script_row(row, row_errors, install_path):
    append_required_value_errors(
        row,
        row_errors,
        ['步骤', '脚本路径', '是否幂等', '是否依赖', '执行机类型'],
    )
    if not os.path.exists(os.path.join(install_path, row['脚本路径'])):
        row_errors['脚本路径'].append(CheckError(ErrorType.PATH_NOT_EXIST))
    if row['是否幂等'] not in {'是', '否'}:
        row_errors['是否幂等'].append(CheckError(ErrorType.BOOL_ERROR))
    if row['是否依赖'] not in {'是', '否'}:
        row_errors['是否依赖'].append(CheckError(ErrorType.BOOL_ERROR))
    if row['执行机类型'] not in {'宿主机', '容器'}:
        row_errors['执行机类型'].append(CheckError(ErrorType.SCRIPT_ENV_TYPE_ERROR))


def validate_image_pull_row(row, row_errors, context):
    append_required_value_errors(row, row_errors, ['步骤', context.image_column])
    image = row[context.image_column]
    if image and context.version == 1.1 and not check_ref_digest(image):
        row_errors[context.image_column].append(CheckError(ErrorType.DEPLOY_IMG_REF_ERROR))
    yaml_path = row['YAML路径']
    if yaml_path and not os.path.exists(os.path.join(context.install_path, yaml_path)):
        row_errors['YAML路径'].append(CheckError(ErrorType.DEPLOY_YAML_NOT_EXIST))


def validate_deploy_order(resource_type, deploy_type, row_errors, context):
    if deploy_type != '更新' or resource_type not in DEPLOY_RESOURCE_TYPE:
        return
    resource_order = DEPLOY_RESOURCE_TYPE[resource_type]
    if not deploy_compare_lte(context.sequence_last, resource_order):
        row_errors['资源类型'].append(CheckError(ErrorType.DEPLOY_ORDER_ERROR))
    context.sequence_last = max(context.sequence_last, resource_order)


def append_deploy_yaml_content_errors(row, row_errors, yaml_path, context):
    resource_type = row['资源类型'].lower()
    resource_name = row['资源名称']
    if resource_type not in DEPLOY_RESOURCE_TYPE or not resource_name:
        return
    try:
        if yaml_path not in context.yaml_documents_cache:
            context.yaml_documents_cache[yaml_path] = tokenize_yaml_placeholders(yaml_path)
        documents, token_key_map = context.yaml_documents_cache[yaml_path]
        if resource_type == 'deployment' and row['部署类型'] in {'重启', '下线'}:
            if not any(isinstance(document, dict) and isinstance(document.get('kind'), str)
                       and check_k8s_kind_same(resource_type, document['kind']) for document in documents):
                row_errors['资源类型'].append(CheckError(ErrorType.DEPLOY_YAML_KIND_ERROR))
                return
            row_errors['资源名称'].extend(check_deploy_resource_names(
                row['资源类型'], resource_name, documents, token_key_map, context.global_var_values,
                check_containers=False,
            ))
            _, document, _, _ = resolve_deploy_resource(
                row['资源类型'], resource_name, documents, token_key_map, context.global_var_values,
            )
            if document is not None:
                metadata = document.get('metadata')
                namespace = metadata.get('namespace') if isinstance(metadata, dict) else None
                actual_namespace, missing_actual = resolve_yaml_name(namespace, token_key_map, context.global_var_values)
                expected_namespace, missing_expected = resolve_template_text(row['命名空间'], context.global_var_values)
                if not missing_actual and not missing_expected and actual_namespace != expected_namespace:
                    row_errors['命名空间'].append(CheckError(
                        ErrorType.DEPLOY_NAMESPACE_ERROR, (actual_namespace or '<未填写>',),
                    ))
            return
        row_errors['资源名称'].extend(check_deploy_resource_names(
            row['资源类型'], resource_name, documents, token_key_map, context.global_var_values,
        ))
        row_errors['YAML路径'].extend(check_deployment_health_probes(
            row['资源类型'], resource_name, documents, token_key_map, context.global_var_values,
        ))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        # 解析失败时无法可靠比较名称；不要把格式问题误报成名称不一致。
        pass


def validate_deploy_yaml(row, row_errors, context):
    relative_path = row['YAML路径']
    yaml_path = os.path.join(context.install_path, relative_path)
    if not relative_path or not os.path.exists(yaml_path):
        row_errors['YAML路径'].append(CheckError(ErrorType.DEPLOY_YAML_NOT_EXIST))
    else:
        append_deploy_yaml_content_errors(row, row_errors, yaml_path, context)

    filename, extension = os.path.splitext(relative_path)
    yaml_kind = os.path.basename(filename).split('-')[-1]
    if not check_k8s_kind_same(yaml_kind, row['资源类型']) or extension.lower() not in {'.yaml', '.yml'}:
        row_errors['YAML路径'].append(CheckError(ErrorType.DEPLOY_YAML_NAME_ERROR))


def validate_deploy_image(row, row_errors, resource_type, context):
    if resource_type not in DEPLOY_NEED_IMAGE:
        return
    image = row[context.image_column]
    if image and context.version == 1.1 and not check_ref_digest(image):
        row_errors[context.image_column].append(CheckError(ErrorType.DEPLOY_IMG_REF_ERROR))
    elif not image and row['部署类型'] not in {'下线', '重启'}:
        row_errors[context.image_column].append(CheckError(ErrorType.VALUE_NOT_EXIST))


def validate_deploy_row(row, row_errors, context):
    resource_type = row['资源类型'].lower()
    if (context.image_column == '镜像引用digest'
            and resource_type in DEPLOY_RESOURCE_TYPE
            and resource_type not in DEPLOY_NEED_IMAGE
            and row[context.image_column]):
        row_errors[context.image_column].append(CheckError(ErrorType.DEPLOY_UNNEEDED_IMAGE_ERROR))

    if row['部署类型'] == '镜像拉取':
        validate_image_pull_row(row, row_errors, context)
        return

    append_required_value_errors(
        row,
        row_errors,
        ['步骤', '资源类型', '资源名称', '命名空间', '部署类型'],
    )
    if resource_type not in DEPLOY_RESOURCE_TYPE:
        row_errors['资源类型'].append(CheckError(ErrorType.DEPLOY_RESOURCE_TYPE_ERROR))
    if row['部署类型'] not in DEPLOY_TYPE:
        row_errors['部署类型'].append(CheckError(ErrorType.DEPLOY_TYPE_ERROR))

    validate_deploy_order(resource_type, row['部署类型'], row_errors, context)
    validate_deploy_yaml(row, row_errors, context)
    validate_deploy_image(row, row_errors, resource_type, context)


def validate_local_row(row, row_errors, control_type: str, install_path):
    """属地替换只检查变量键及实际引用的文件，不执行发布规范校验。"""
    if control_type == 'var':
        append_required_value_errors(row, row_errors, ['变量键（KEY）'])
        return
    path_column = 'YAML路径' if control_type == 'deploy' else '脚本路径'
    relative_path = row[path_column]
    if control_type == 'script':
        append_required_value_errors(row, row_errors, [path_column])
    if relative_path and not os.path.isfile(os.path.join(install_path, relative_path)):
        error_type = ErrorType.DEPLOY_YAML_NOT_EXIST if control_type == 'deploy' else ErrorType.PATH_NOT_EXIST
        row_errors[path_column].append(CheckError(error_type))


def load_data_from_csv(filepath: str, control_type='var', *, strict=True):
    """读取中控 CSV；默认严格校验，属地替换仅校验结构、变量键和文件引用。"""
    install_path = os.path.dirname(os.path.dirname(filepath))
    line_data = read_controls_csv(filepath)
    column_names, version = get_control_csv_schema(line_data, control_type)
    data_lines = line_data[1:]
    row_num, col_num = len(data_lines), len(column_names)
    error_mask = [[[] for _ in range(col_num)] for _ in range(row_num)]
    empty_idx_set = {
        index for index, row in enumerate(data_lines)
        if is_empty_csv_row(row)
    }
    deploy_context = None
    if strict and control_type == 'deploy':
        deploy_context = DeployValidationContext(
            install_path=install_path,
            version=version,
            image_column='镜像引用digest' if '镜像引用digest' in column_names else '镜像包名称',
            global_var_values=load_global_var_values(install_path),
        )

    seen_steps = {}
    for index, values in enumerate(data_lines):
        if index in empty_idx_set:
            continue
        row = map_row_by_columns(column_names, values)
        row_errors = dict(zip(column_names, error_mask[index]))
        if strict and control_type in {'deploy', 'script'}:
            validate_plan_step(row, row_errors, seen_steps, index + 1)
        if len(values) > col_num:
            row_errors[column_names[-1]].append(CheckError(
                ErrorType.CSV_EXTRA_COLUMNS_ERROR,
                (len(values), col_num, ', '.join(values[col_num:])),
            ))
        if not strict:
            validate_local_row(row, row_errors, control_type, install_path)
        elif control_type == 'var':
            validate_var_row(row, row_errors)
        elif deploy_context is not None:
            validate_deploy_row(row, row_errors, deploy_context)
        elif control_type == 'script':
            validate_script_row(row, row_errors, install_path)

    error_logs = generate_csv_logs(error_mask, line_data)
    return data_lines, empty_idx_set, (error_mask, error_logs), version


def read_controls_csv(filepath: str):
    """
    读取中控类csv文件
    """
    try:
        with open(filepath, 'r', newline='', encoding='utf-8-sig') as csvfile:
            content = csv.reader(csvfile)
            content = [row for row in content]
            return content
    except UnicodeDecodeError as exc:
        raise ValueError('中控类文件编码不在支持范围内，请转换为 utf-8 编码！') from exc


def decode_key(v):
    try:
        return v.decode()
    except UnicodeDecodeError as e:
        raise e


def stream_replace(template_path, variables, replace_mode='字符串和控制符', check: bool = True, is_yaml: bool = False,
                   chunk_size: int = 1024 * 1024 * 100, validator=None) -> Tuple[int, Set[bytes], Set[bytes]]:
    """
    流式读取 -> 占位符替换 -> 流式写入新文件。
    replacer: 接收占位符“中间内容”（bytes，不含边界标记），返回 bytes。
    返回: (替换次数, matched_keys, missing_keys)
    """
    pattern, start_tokens = PATTERN_MAP[replace_mode], START_TOKENS_MAP[replace_mode]
    missing_keys, matched_keys, replace_num = set(), set(), 0

    def _replacer(match):
        key = decode_key(match.group(1) or match.group(2))
        nonlocal replace_num
        if key in variables:
            replace_num += 1
            matched_keys.add(key)
            return variables[key].encode()
        else:
            missing_keys.add(key)
            return match.group(0)

    def _write(f, contents):
        if not check or validator is not None or is_yaml:
            f.write(contents)

    buf = b""
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = os.path.join(temp_dir, 'replaced' + Path(template_path).suffix.lower())
        with open(template_path, 'rb') as fin, open(output_path, 'wb') as fout:
            while True:
                chunk = fin.read(chunk_size)
                buf += chunk
                # 处理当前缓冲中的完整匹配
                write_pos = 0
                for m in pattern.finditer(buf):
                    replace_text = _replacer(m)
                    _write(fout, buf[write_pos:m.start()])
                    _write(fout, replace_text)
                    write_pos = m.end()
                if not chunk:
                    _write(fout, buf[write_pos:])
                    break
                # 尾巴处理, 找尾巴里“最后一个起始 token”的位置（同型），把它之后的内容留到下轮
                tail = buf[write_pos:]
                last_start = max((tail.rfind(st) for st in start_tokens), default=-3)
                buf = tail[last_start:]
                _write(fout, tail[:last_start])
        if validator is not None and not missing_keys:
            validator(output_path)
        if is_yaml:
            check_k8s_yaml_valid(output_path)
        if not check:
            shutil.copyfile(output_path, template_path)
    return replace_num, matched_keys, missing_keys


def replace_placeholders_in_file(template_path, variables: dict, defined_keys: set, replace_mode='字符串和控制符',
                                 check=True, is_yaml=False, validator=None):
    """
    替换文件中的占位符
    :param replace_mode:
    :param template_path:
    :param variables:
    :param defined_keys:
    :param check:
    :param is_yaml: is k8s yaml
    :return:
    """
    matched_keys, missing_keys, status, replace_num, exist_vars_fp, logs = set(), set(), False, 0, [], []
    try:
        replace_num, matched_keys, missing_keys = stream_replace(
            template_path, variables, replace_mode, check, is_yaml, validator=validator,
        )
        status = True
        logs.append([f'成功，替换位置 {replace_num} 个', logging.INFO])
        undefined_path_keys = matched_keys.difference(defined_keys)
        defined_keys.difference_update(matched_keys)
        defined_keys.update(undefined_path_keys)
        missing_keys and logs.append([f'发现未定义变量！！！请确认: {missing_keys}', logging.WARNING])
        if validator is not None and missing_keys:
            logs.append(['存在未定义变量，已跳过脚本语法检查。', logging.WARNING])
    except UnicodeDecodeError as e:
        level = logging.ERROR if validator is not None else logging.WARNING
        logs.append([f'文件编码错误：{e}' if validator is not None else f'警告：warning: {e}', level])
    except Exception as e:
        logs.append([f'失败：error: {str(e)}', logging.ERROR])
    return matched_keys, missing_keys, status, replace_num, logs


def fix_global_csv(csv_path, var_fps_map):
    with (open(csv_path, newline='', encoding='utf-8-sig') as fin,
          tempfile.NamedTemporaryFile('w', newline='', encoding='utf-8-sig', delete=False) as fout):
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=reader.fieldnames)
        writer.writeheader()
        for data_row_index, row in enumerate(reader, start=1):
            extra_values = row.pop(None, None)
            if extra_values is not None:
                raise ValueError(
                    f'global-vars.csv 第 {data_row_index} 条数据有多余字段【{", ".join(extra_values)}】。'
                    '如字段内容包含英文逗号，请使用双引号包裹！'
                )
            key = row['变量键（KEY）']
            row['文件路径'] = '\n'.join(var_fps_map[key]) if key in var_fps_map else row['文件路径']
            writer.writerow(row)
    shutil.copyfile(fout.name, csv_path)


def query_k8s_yaml_paths(install_dir):
    yaml_paths_set = set()
    line_data = read_controls_csv(os.path.join(install_dir, 'controls/deploy-execution-plan.csv'))
    if not line_data:
        return yaml_paths_set
    column_names = line_data[0]
    for values in line_data[1:]:
        row = map_row_by_columns(column_names, values)
        yaml_path = row.get('YAML路径', '')
        if not yaml_path:
            continue
        k8s_yaml_path = os.path.join(install_dir, yaml_path)
        os.path.exists(k8s_yaml_path) and yaml_paths_set.add(k8s_yaml_path)
    return yaml_paths_set


def check_k8s_yaml_valid(yaml_path):
    path = Path(yaml_path)
    with path.open("r", encoding="utf-8") as f:
        for _ in yaml.safe_load_all(f):
            pass


def check_yaml_syntax(path):
    """检查 YAML 全部文档的语法，不要求文档符合特定数据结构。"""
    with Path(path).open('r', encoding='utf-8-sig') as stream:
        try:
            for _ in yaml.parse(stream):
                pass
        except yaml.YAMLError as exc:
            mark = getattr(exc, 'problem_mark', None)
            location = f'第 {mark.line + 1} 行第 {mark.column + 1} 列' if mark else ''
            raise ValueError(f'YAML {location}语法检查未通过') from exc


def check_json_syntax(path):
    """按标准 JSON 解析整个文件，包括结尾是否有多余内容。"""

    def reject_constant(value):
        raise ValueError(f'JSON 包含非法常量 {value}，语法检查未通过')

    with Path(path).open('r', encoding='utf-8-sig') as stream:
        try:
            json.load(stream, parse_constant=reject_constant)
        except json.JSONDecodeError as exc:
            raise ValueError(f'JSON 第 {exc.lineno} 行第 {exc.colno} 列语法检查未通过') from exc


def detect_sql_dialect(source):
    """从 SQL 语法特征推断方言，忽略字符串和注释中的关键词。"""
    code = SQL_NON_CODE_RE.sub(' ', source)
    matches = {
        dialect for dialect, pattern in SQL_DIALECT_PATTERNS.items()
        if re.search(pattern, code, re.IGNORECASE | re.MULTILINE)
    }
    # MySQL 的 DELIMITER $$ 不是 PostgreSQL 美元引号。
    if 'DELIMITER' not in code.upper() and re.search(r'\$[A-Za-z_0-9]*\$', code):
        matches.add('postgres')
    if len(matches) > 1:
        raise ValueError(
            f'检测到多种 SQL 方言特征（{", ".join(sorted(matches))}），'
            '请设置 INSTALL_AGENT_SQL_DIALECT 明确指定方言'
        )
    if matches:
        return matches.pop()
    # 反引号和方括号也可用于其他数据库，只在没有更强特征时使用。
    if re.search(r'`[^`]+`|\bREPLACE\s+INTO\b', code, re.IGNORECASE):
        return 'mysql'
    if re.search(r'\[[A-Za-z_][^\]]*\]', code):
        return 'tsql'
    return 'ansi'


def get_positive_int_env(name, default):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f'{name} 必须是正整数') from exc
    if value <= 0:
        raise ValueError(f'{name} 必须是正整数')
    return value


def iter_sql_chunk_ranges(source, max_chars=DEFAULT_SQL_CHUNK_CHARS, dialect='ansi'):
    """按方言识别完整语句边界，返回每块的原文起止位置和起始行号。"""
    start = 0
    start_line = line = 1
    state = None
    dollar_quote = None
    comment_depth = 0
    block_depth = 0
    case_depth = 0
    oracle_await_slash = False
    delimiter = ';'
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ''
        if char == '\n':
            line += 1
        if state in ("'", '"', '`'):
            if char == '\\' and next_char:
                index += 1
                if source[index] == '\n':
                    line += 1
            elif char == state:
                if next_char == state:
                    index += 1
                else:
                    state = None
        elif state == 'bracket':
            if char == ']':
                if next_char == ']':
                    index += 1
                else:
                    state = None
        elif state == 'dollar_quote':
            if source.startswith(dollar_quote, index):
                index += len(dollar_quote) - 1
                state = None
                dollar_quote = None
        elif state == 'line_comment':
            if char == '\n':
                state = None
        elif state == 'block_comment':
            if char == '/' and next_char == '*':
                comment_depth += 1
                index += 1
            elif char == '*' and next_char == '/':
                comment_depth -= 1
                index += 1
                if comment_depth == 0:
                    state = None
        elif dialect == 'mysql' and (index == 0 or source[index - 1] == '\n') and (
                directive := MYSQL_DELIMITER_LINE_RE.match(source, index)):
            delimiter = directive.group(1)
            index = directive.end() - 1
            if source[index] == '\n':
                line += 1
        elif dialect == 'oracle' and oracle_await_slash and (
                index == 0 or source[index - 1] == '\n') and (
                slash := ORACLE_SLASH_LINE_RE.match(source, index)):
            end = slash.end()
            line += source[index:end].count('\n')
            if end - start >= max_chars:
                yield start_line, start, end
                start = end
                start_line = line
            oracle_await_slash = False
            index = end - 1
        elif char in ("'", '"', '`'):
            state = char
        elif dialect == 'tsql' and char == '[':
            state = 'bracket'
        elif dialect == 'postgres' and char == '$' and (
                quote := SQL_DOLLAR_QUOTE_RE.match(source, index)):
            dollar_quote = quote.group()
            state = 'dollar_quote'
            index = quote.end() - 1
        elif (dialect == 'mysql' and char == '#') or (char == '-' and next_char == '-' and
                                                      (dialect != 'mysql' or index + 2 == len(source) or source[
                                                          index + 2].isspace())):
            state = 'line_comment'
            if char == '-':
                index += 1
        elif char == '/' and next_char == '*':
            state = 'block_comment'
            comment_depth = 1
            index += 1
        elif dialect in {'oracle', 'tsql'} and (word_match := SQL_WORD_RE.match(source, index)):
            word = word_match.group().upper()
            if word == 'CASE':
                case_depth += 1
            elif word == 'BEGIN':
                if dialect != 'tsql' or not TSQL_TRANSACTION_RE.match(source, word_match.end()):
                    block_depth += 1
            elif word == 'END':
                if case_depth:
                    case_depth -= 1
                elif block_depth and not SQL_END_CLAUSE_RE.match(source, word_match.end()):
                    block_depth -= 1
                    if dialect == 'oracle' and block_depth == 0:
                        oracle_await_slash = True
            index = word_match.end() - 1
        elif source.startswith(delimiter, index) and not block_depth and not oracle_await_slash:
            end = index + len(delimiter)
            if end - start >= max_chars:
                yield start_line, start, end
                start = end
                start_line = line
            index = end - 1
        index += 1
    if start < len(source):
        yield start_line, start, len(source)


def iter_mysql_sql_chunks(source, max_chars=DEFAULT_SQL_CHUNK_CHARS):
    """兼容原有调用；新的分割器也支持其他 SQL 方言。"""
    for start_line, start, end in iter_sql_chunk_ranges(source, max_chars, dialect='mysql'):
        yield start_line, source[start:end]


def normalize_mysql_sql_for_check(source):
    """仅调整检查副本，让 SQLFluff 能解析 MySQL 客户端分隔符和中文标识符。"""
    parts = []
    start = 0
    for match in SQL_NON_CODE_RE.finditer(source):
        parts.append(''.join('x' if ord(char) > 127 else char for char in source[start:match.start()]))
        parts.append(match.group())
        start = match.end()
    parts.append(''.join('x' if ord(char) > 127 else char for char in source[start:]))
    normalized = ''.join(parts)

    # 保留原有字符数和换行，使 SQLFluff 的错误位置仍对应物料原文。
    code = SQL_NON_CODE_RE.sub(
        lambda match: re.sub(r'[^\r\n]', ' ', match.group()), normalized,
    )
    delimiter = ';'
    lines = []
    for line, code_line in zip(normalized.splitlines(keepends=True), code.splitlines(keepends=True)):
        newline = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
        body = line[:-len(newline)] if newline else line
        code_body = code_line[:-len(newline)] if newline else code_line
        directive = re.fullmatch(r'[ \t]*DELIMITER[ \t]+(\S+)[ \t]*', body, re.IGNORECASE)
        if directive and re.match(r'[ \t]*DELIMITER\b', code_body, re.IGNORECASE):
            delimiter = directive.group(1)
            lines.append(' ' * len(body) + newline)
            continue
        code_end = len(code_body.rstrip(' \t'))
        if delimiter != ';' and code_body[:code_end].endswith(delimiter):
            position = code_end - len(delimiter)
            body = body[:position] + ';' + ' ' * (len(delimiter) - 1) + body[code_end:]
        lines.append(body + newline)
    return ''.join(lines)


def normalize_mysql_transaction_begins_for_check(chunk):
    """把独立事务 BEGIN 改为 SQLFluff 可识别的等价语句，不触碰注释和字符串。"""
    code = SQL_NON_CODE_RE.sub(
        lambda match: re.sub(r'[^\r\n]', ' ', match.group()), chunk,
    )
    for match in reversed(list(MYSQL_TRANSACTION_BEGIN_RE.finditer(code))):
        start, end = match.span(1)
        chunk = chunk[:start] + 'START TRANSACTION' + chunk[end:]
    return chunk


def check_sql_syntax(path, source_path=None):
    """先识别方言，再用 SQLFluff 解析替换后的 SQL，不执行语句。"""
    # 属地替换脚本不检查 SQL，只有显式调用 SQL 检查时才需要 SQLFluff。
    from sqlfluff.core import FluffConfig, Linter

    original_source = Path(path).read_text(encoding='utf-8-sig')
    source_dir = Path(source_path or path).parent.name.lower()
    dialect = (
            os.environ.get('INSTALL_AGENT_SQL_DIALECT')
            or {'mysql': 'mysql', 'vsql': 'postgres', 'vastsql': 'postgres'}.get(source_dir)
            or detect_sql_dialect(original_source)
    )
    source = normalize_mysql_sql_for_check(original_source) if dialect == 'mysql' else original_source
    max_parse_nodes = get_positive_int_env('INSTALL_AGENT_SQL_MAX_PARSE_NODES', DEFAULT_SQL_MAX_PARSE_NODES)
    chunk_chars = get_positive_int_env('INSTALL_AGENT_SQL_CHUNK_CHARS', DEFAULT_SQL_CHUNK_CHARS)
    config = FluffConfig(overrides={
        'dialect': dialect, 'templater': 'raw', 'max_parse_nodes': max_parse_nodes,
    })
    linter = Linter(config=config)
    for start_line, start, end in iter_sql_chunk_ranges(original_source, chunk_chars, dialect):
        chunk = source[start:end]
        if dialect == 'mysql':
            chunk = normalize_mysql_transaction_begins_for_check(chunk)
        result = linter.parse_string(chunk, fname=path)
        if result.violations:
            violation = result.violations[0]
            if 'Maximum parse node count exceeded' in violation.desc():
                raise ValueError(
                    f'SQLFluff 解析节点数超过 {max_parse_nodes}，无法完成语法检查；'
                    '请调小 INSTALL_AGENT_SQL_CHUNK_CHARS，或根据实际 SQL 调整 '
                    'INSTALL_AGENT_SQL_MAX_PARSE_NODES'
                )
            line_no = start_line + violation.line_no - 1
            raise ValueError(
                f'SQL 第 {line_no} 行第 {violation.line_pos} 列语法检查未通过'
                f'（SQLFluff，方言 {dialect}）'
            )


def check_script_syntax(path, source_path=None):
    suffix = Path(path).suffix.lower()
    if suffix == '.json':
        check_json_syntax(path)
        return
    if suffix in {'.yaml', '.yml'}:
        check_yaml_syntax(path)
        return
    if suffix == '.sql':
        # 暂停 SQL 文件语法检查，后续需要恢复时取消下一行注释。
        # check_sql_syntax(path, source_path=source_path)
        return
    command = ['node', '--check', path] if suffix == '.js' else ['bash', '-n', path]
    if not shutil.which(command[0]):
        raise RuntimeError(f'缺少 {command[0]}，无法检查 {suffix} 文件语法')
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        failed = result.returncode != 0 or bool(result.stderr.strip())
        # bash -n 不执行脚本中的 shopt，因而无法识别随后使用的 extglob 模式。
        # 只在脚本显式启用 extglob 且首次解析失败时，带该选项重新解析。
        if failed and suffix != '.js':
            source = Path(path).read_bytes()
            enables_extglob = re.search(
                rb'(?m)^[ \t]*shopt[ \t]+-s(?:[ \t]+[A-Za-z_][A-Za-z_0-9]*)*'
                rb'[ \t]+extglob\b', source,
            )
            if enables_extglob:
                command = ['bash', '-O', 'extglob', '-n', path]
                result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
                failed = result.returncode != 0 or bool(result.stderr.strip())
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f'{suffix} 文件语法检查超时') from exc
    if failed:
        match = re.search(r':(?: line )?(\d+)(?::\d+)?(?:\D|$)', result.stderr)
        location = f'第 {match.group(1)} 行' if match else ''
        raise ValueError(f'{suffix} 文件{location}语法检查未通过（{command[0]}）')


def dispose_controls(install_dir, replace_mode='字符串和控制符', check=True, dispose_fps=(), *, strict=True):
    """
    处理中控类文件
    :param install_dir: 交付物目录路径
    :param replace_mode: 替换模式，字符串和控制符、仅控制符
    :param check: 是否检查
    :param dispose_fps: 需要处理的模版文件
    :param strict: 发布检查启用脚本语法校验；属地模式只保留基本检查，不调用外部工具
    :return:
    """
    deploy_error_ret, script_error_ret = check_standard(install_dir, strict=strict)
    total_logs, file_logs = [], []
    template_paths = query_template_paths(install_dir) if check else dispose_fps
    deploy_yaml_paths = query_k8s_yaml_paths(install_dir)
    all_vars, empty_idx_set, var_error_ret, version = load_data_from_csv(
        os.path.join(install_dir, 'controls/global-vars.csv'), strict=strict)
    vars_map, file_vars_map = dict(), dict()
    var_column_names = CONTROL_COLUMNS_MAP['var'][0][0].split(' ')
    for index, values in enumerate(all_vars):
        if index in empty_idx_set:
            continue
        row = map_row_by_columns(var_column_names, values)
        key = row['变量键（KEY）']
        assert key not in vars_map.keys(), '全局变量 {} 重复定义，请检查并更换变量名称！！！'.format(key)
        vars_map[key] = row['填写示例']
        var_files = row['文件路径'].splitlines() if row['文件路径'] else []
        var_files = len(var_files) == 1 and var_files[0].split(',') or var_files
        [file_vars_map.setdefault(os.path.join(install_dir, vf), set()).add(key) for vf in var_files]
    all_missing_keys, all_matched_keys, all_defined_keys = set(), set(), set()
    # 替换成功的文件数量，替换成功的变量数量
    ok_file_num, all_replace_num, warn_fp, error_fp = 0, 0, [], []
    # 变量:文件路径 map
    var_fps_map = dict()
    for fp in template_paths:
        defined_keys = file_vars_map.get(fp, set())
        relative_parts = Path(os.path.relpath(fp, install_dir)).parts
        validator = (
            partial(check_script_syntax, source_path=fp)
            if strict and relative_parts[0] == 'scripts' and Path(fp).suffix.lower() in {
                '.js', '.sql', '.sh', '.yaml', '.yml', '.json',
            }
            else None
        )
        if not strict and Path(fp).suffix.lower() in {'.yaml', '.yml'}:
            validator = check_yaml_syntax
        replace_ret = replace_placeholders_in_file(fp, vars_map, defined_keys, replace_mode, check,
                                                   fp in deploy_yaml_paths, validator=validator)
        matched_keys, missing_keys, status, replace_num, replace_logs = replace_ret
        for l in replace_logs:
            if l[1] == logging.WARNING:
                warn_fp.append(os.path.relpath(fp, install_dir))
            elif l[1] == logging.ERROR:
                error_fp.append(os.path.relpath(fp, install_dir))
        # 获取变量所在的文件相对路径
        [var_fps_map.setdefault(mk, set()).add(os.path.relpath(fp, install_dir)) for mk in matched_keys]
        all_matched_keys = all_matched_keys.union(matched_keys)
        all_missing_keys = all_missing_keys.union(missing_keys)
        all_defined_keys = all_defined_keys.union(defined_keys)
        ok_file_num += status
        all_replace_num += replace_num
        file_logs.append([os.path.relpath(fp, install_dir), replace_logs])
    unused_keys = vars_map.keys() - all_matched_keys
    total_logs.append(['', logging.INFO])
    total_logs.append([f'共计处理 {len(template_paths)} 个模版文件，成功 {ok_file_num} 个，替换位置 {all_replace_num} 个，'
                       f'失败 {len(template_paths) - ok_file_num} 个！', logging.INFO])
    total_logs.append([f'全局变量表中共计定义 {len(vars_map.keys())} 个变量，成功替换 {len(all_matched_keys)} 个，'
                       f'有 {len(unused_keys)} 个已定义未替换！有 {len(all_missing_keys)} 个未定义未替换！(后两项正常应为 0 )',
                       logging.INFO])
    unused_keys and total_logs.append(
        [f'以下变量在全局变量表中定义，但未在部署模版中发现！如无需使用请删除，避免引入无关变量，'
         f'请确认！！！  \n{unused_keys}', logging.WARNING])
    all_missing_keys and total_logs.append(
        [f'以下变量在模版文件中发现，但未在全局变量表中定义！替换变量时将无法处理这些占位符，'
         f'请确认！！！  \n{all_missing_keys}', logging.WARNING])
    warn_fp and total_logs.append([f'处理以下模版文件时出现警告，请关注日志！  \n{warn_fp}', logging.WARNING])
    error_fp and total_logs.append(
        [f'以下模版文件检查失败，可能脚本语法、文件编码或变量替换错误。'
         f'请查看下方对应文件的详细日志并修复：  \n{error_fp}', logging.ERROR])
    return [deploy_error_ret, script_error_ret, var_error_ret], file_logs, total_logs, var_fps_map


def exec_replace(install_dir, replace_mode='字符串和控制符', check=False,
                 old_image_prefix=None, new_image_prefix=None, *, strict=True) -> tuple[bool, list[list[str | int] | Any]]:
    """
    :param install_dir: 物料路径
    :param replace_mode: 字符串和控制符 or 仅控制符
    :param check: 是否检查模式
    :param old_image_prefix:
    :param new_image_prefix:
    :return: (脚本执行状态，日志及级别）
    """
    exist_error, logs = False, []
    error_log_titles = [['deploy-execution-plan.csv 检查失败：', logging.ERROR],
                        ['script-execution-plan.csv 检查失败：', logging.ERROR],
                        ['global-vars.csv 检查失败：', logging.ERROR]]
    try:
        error_rets, file_logs, total_logs, var_fps_map = dispose_controls(install_dir, replace_mode, True, strict=strict)
        for i, error_ret in enumerate(error_rets):
            mask, error_logs = error_ret
            if error_logs:
                exist_error = True
                logs.append(error_log_titles[i])
                logs.extend([[e, logging.ERROR] for e in error_logs])
        assert not exist_error, '中控类文件检查失败!'
        for file_log in file_logs:
            logs.append([f'-----------------  检查文件:{file_log[0]}  -----------------', logging.INFO])
            logs.extend(file_log[1])
            for l in file_log[1]:
                if l[1] == logging.ERROR:
                    exist_error = True
                    break
        assert not exist_error, '模版文件检查失败!'
        logs.extend(total_logs)
        if not check:
            fix_global_csv(os.path.join(install_dir, 'controls/global-vars.csv'), var_fps_map)
            dispose_fps = set()
            for fps in var_fps_map.values():
                dispose_fps.update([os.path.join(install_dir, rp) for rp in fps])
            dispose_controls(install_dir, replace_mode, False, dispose_fps, strict=strict)
            replace_k8s_images(install_dir, old_image_prefix, new_image_prefix)
        return True, [*logs, ['全局变量替换工具执行成功！', logging.INFO]]
    except Exception as e:
        return False, [*logs, [str(e), logging.ERROR], ['全局变量替换工具执行失败！', logging.ERROR]]


def set_logger():
    # 设置日志格式
    log_format = '%(asctime)s || %(levelname)s || %(message)s'
    log_level = logging.INFO
    logger = logging.getLogger()
    logger.setLevel(log_level)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    file_handler = logging.FileHandler("replace_vars.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger


def get_real_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main(install_dir, replace_mode='字符串和控制符', check=False, old_image_prefix=None, new_image_prefix=None):
    log = set_logger()
    try:
        status, logs_with_level = exec_replace(install_dir, replace_mode, check, old_image_prefix, new_image_prefix)
        [log.log(l[1], l[0]) for l in logs_with_level]
    except Exception as e:
        log.exception(e)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='run replace script.')
    parser.add_argument('install_dir', nargs='?', default=get_real_app_dir(), help='交付物料包路径')
    parser.add_argument('--check', action='store_true', help='是否检查模式')
    parser.add_argument('--replace_mode', choices=['字符串和控制符', '仅控制符'], default='字符串和控制符',
                        help='变量替换模式，(字符串和控制符 or 仅控制符)')
    parser.add_argument('--old_image_prefix', default=DEFAULT_IMAGE_PREFIX, help='待匹配的镜像前缀')
    parser.add_argument('--new_image_prefix', default=DEFAULT_IMAGE_PREFIX, help='待替换的镜像前缀')
    args = parser.parse_args()
    main(args.install_dir, replace_mode=args.replace_mode, check=args.check,
         old_image_prefix=args.old_image_prefix, new_image_prefix=args.new_image_prefix)

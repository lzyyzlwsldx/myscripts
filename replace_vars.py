# -*- coding: utf-8 -*-

import os
import re
import csv
import sys
import shutil
import logging
import tempfile
import argparse
from typing import List, Tuple, Dict, Set

CONTROL_COLUMNS_MAP = {'var': '序号 变量键（KEY） 变量描述 变量类型 填写示例 文件路径 填写说明',
                       'deploy': '步骤 资源类型 资源名称 命名空间 部署类型 YAML路径 镜像包名称 备注',
                       'script': '步骤 脚本路径 是否幂等 是否依赖 执行机类型 执行用户 K8S命名空间 负载资源名称 备注'}
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
DEPLOY_TYPE = {'更新', '下线', '重启'}
# global-vars.csv columns
VAR_TYPE = {'字符串', '数值', '布尔'}
# script-execution-plan.csv columns
SCRIPT_COLUMNS = '步骤 脚本路径 是否幂等 是否依赖 执行机类型 执行用户 K8S命名空间 负载资源名称 备注'
CONTROL_TYPE_MAP = {'var': 'global-vars', 'deploy': 'deploy-execution-plan', 'script': 'script-execution-plan'}
# 两种成对匹配：\x02…\x03 或 \\x02…\\x03，最长64个
PATTERN_MAP = {'字符串和控制符': re.compile(br'\x02(.{1,64}?)\x03|\\x02(.{1,64}?)\\x03', re.DOTALL),
               '仅控制符': re.compile(br'\x02(.{1,64}?)\x03', re.DOTALL)}
START_TOKENS_MAP: Dict[str, Tuple[bytes, ...]] = {'字符串和控制符': (b'\x02', b'\\x02'), '仅控制符': (b'\x02',), }


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
}


class CheckError(object):
    def __init__(self, error_type, error_values=()):
        self.error_type = error_type
        self.error_values = error_values
        self.error_msg = ERROR_LOG_MAP[error_type].format(*error_values)


def check_k8s_kind_same(n1, n2):
    """检查的两个类型名字是否相同"""
    n1_lower, n2_lower = n1.lower(), n2.lower()
    for i in K8S_ABBR:
        if n1_lower in i and n2_lower in i:
            return True
    return n1_lower == n2_lower


def check_standard(install_dir):
    assert os.path.exists(install_dir), '{} 部署包目录不存在，请输入正确路径！'.format(install_dir)
    assert os.path.exists(os.path.join(install_dir, 'controls/global-vars.csv')), '未发现【全局变量清单】global-vars.csv，请确认！！！'
    deploy_error_ret, script_error_ret = [[], [], []], [[], [], []]
    deploy_csv_path = os.path.join(install_dir, 'controls/deploy-execution-plan.csv')
    if os.path.exists(deploy_csv_path):
        _, _, deploy_error_ret = load_data_from_csv(deploy_csv_path, 'deploy')
    script_csv_path = os.path.join(install_dir, 'controls/script-execution-plan.csv')
    if os.path.exists(script_csv_path):
        _, _, script_error_ret = load_data_from_csv(script_csv_path, 'script')
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
    error_pos_log = lambda x, y: '第 {} 行 {} 列，【{}】 {}'.format(x + 1, y + 1, cols[y], data[x][y])
    for ri in range(len(error_mask)):
        for ci in range(len(error_mask[ri])):
            error_list = error_mask[ri][ci]
            error_logs.extend([f'{error_pos_log(ri, ci)} {v.error_msg}' for v in error_list])
            error_mask[ri][ci] = error_list and error_list[0] or CheckError(ErrorType.SUCCESS)
    return error_logs


def load_data_from_csv(filepath: str, control_type='var'):
    """
    读取全局变量字典
    :param control_type: var, deploy, script
    :param filepath:
    :return:
    """
    install_path = os.path.dirname(os.path.dirname(filepath))
    line_data = read_controls_csv(filepath)
    assert len(line_data), '{} 文件格式有误，未发现列名！'.format(CONTROL_TYPE_MAP[control_type])
    assert ' '.join(line_data[0]) == CONTROL_COLUMNS_MAP[control_type], \
        '{} 文件列名不匹配！列名应为 {}'.format(CONTROL_TYPE_MAP[control_type], CONTROL_COLUMNS_MAP[control_type])
    data_lines = line_data[1:]
    row_num, col_num = len(data_lines), len(CONTROL_COLUMNS_MAP[control_type].split(' '))
    error_mask = [[[] for _ in range(col_num)] for _ in range(row_num)]
    # 查询空行
    empty_idx_set = {
        i for i, row in enumerate(data_lines)
        if all(str(cell).strip() == '' or cell is None for cell in row)
    }
    deploy_seq_last = 0
    for idx, row in enumerate(data_lines):
        if idx in empty_idx_set: continue
        # VAR_COLUMNS = '序号 变量键（KEY） 变量描述 变量类型 填写示例 文件路径 填写说明'
        if control_type == 'var':
            for i in range(len(row[:4])):
                if not row[i]: error_mask[idx][i].append(CheckError(ErrorType.VALUE_NOT_EXIST))
            if row[3] not in VAR_TYPE: error_mask[idx][3].append(CheckError(ErrorType.VAR_TYPE_ERROR))
        # DEPLOY_COLUMNS = '步骤 资源类型 资源名称 命名空间 部署类型 YAML路径 镜像包名称 备注'
        elif control_type == 'deploy':
            for i in range(5):
                if not row[i]: error_mask[idx][i].append(CheckError(ErrorType.VALUE_NOT_EXIST))
            if row[1].lower() not in DEPLOY_RESOURCE_TYPE.keys():
                error_mask[idx][1].append(CheckError(ErrorType.DEPLOY_RESOURCE_TYPE_ERROR))
            if row[4] not in DEPLOY_TYPE:
                error_mask[idx][4].append(CheckError(ErrorType.DEPLOY_TYPE_ERROR))
            if '更新' == row[4]:
                if not deploy_compare_lte(deploy_seq_last, DEPLOY_RESOURCE_TYPE[row[1].lower()]):
                    error_mask[idx][1].append(CheckError(ErrorType.DEPLOY_ORDER_ERROR))
                deploy_seq_last = max(deploy_seq_last, DEPLOY_RESOURCE_TYPE[data_lines[idx][1].lower()])
                # yaml不存在
                if not row[5] or not os.path.exists(os.path.join(install_path, row[5])):
                    error_mask[idx][5].append(CheckError(ErrorType.DEPLOY_YAML_NOT_EXIST))
                filename, ext = os.path.splitext(row[5])
                if not check_k8s_kind_same(os.path.basename(filename).split('-')[-1], row[1]) \
                        or ext[2:] not in {'aml', 'ml'}:
                    error_mask[idx][5].append(CheckError(ErrorType.DEPLOY_YAML_NAME_ERROR))
                # deployment，镜像包
                if row[1].lower() in DEPLOY_NEED_IMAGE and not row[6]:
                    error_mask[idx][6].append(CheckError(ErrorType.VALUE_NOT_EXIST))
        # SCRIPT_COLUMNS = '步骤 脚本路径 是否幂等 是否依赖 执行机类型 执行用户 K8S命名空间 负载资源名称 备注'
        elif control_type == 'script':
            for i in range(5):
                if not row[i]: error_mask[idx][i].append(CheckError(ErrorType.VALUE_NOT_EXIST))
            if not os.path.exists(os.path.join(install_path, row[1])):
                error_mask[idx][1].append(CheckError(ErrorType.PATH_NOT_EXIST))
            if row[2] not in {'是', '否'}: error_mask[idx][2].append(CheckError(ErrorType.BOOL_ERROR))
            if row[3] not in {'是', '否'}: error_mask[idx][3].append(CheckError(ErrorType.BOOL_ERROR))
            if row[4] not in {'宿主机', '容器'}: error_mask[idx][4].append(CheckError(ErrorType.SCRIPT_ENV_TYPE_ERROR))
        else:
            break
    error_logs = generate_csv_logs(error_mask, line_data)
    return data_lines, empty_idx_set, (error_mask, error_logs)


def read_controls_csv(filepath: str):
    try:
        with open(filepath, 'r', newline='', encoding='utf-8-sig') as csvfile:
            content = csv.reader(csvfile)
            content = [row for row in content]
            return content
    except UnicodeDecodeError:
        raise Exception('中控类文件编码不在支持范围内，请转换为 utf-8 编码！')


def decode_key(v):
    try:
        return v.decode()
    except UnicodeDecodeError as e:
        raise e


def stream_replace(template_path, variables, replace_mode='字符串和控制符', check: bool = True,
                   chunk_size: int = 1024 * 1024 * 100, ) -> Tuple[int, Set[bytes], Set[bytes]]:
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
        check or f.write(contents)

    buf = b""
    with open(template_path, 'rb') as fin, tempfile.NamedTemporaryFile('wb', delete=False) as fout:
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
    check or shutil.copyfile(fout.name, template_path)
    return replace_num, matched_keys, missing_keys


def replace_placeholders_in_file(template_path, variables: dict, defined_keys: set, replace_mode='字符串和控制符',
                                 check=True):
    """
    替换文件中的占位符
    :param replace_mode:
    :param template_path:
    :param variables:
    :param defined_keys:
    :param check:
    :return:
    """
    matched_keys, missing_keys, status, replace_num, exist_vars_fp, logs = set(), set(), False, 0, [], []
    try:
        replace_num, matched_keys, missing_keys = stream_replace(template_path, variables, replace_mode, check)
        status = True
        logs.append([f'成功，替换位置 {replace_num} 个', logging.INFO])
        undefined_path_keys = matched_keys.difference(defined_keys)
        defined_keys.difference_update(matched_keys)
        defined_keys.update(undefined_path_keys)
        missing_keys and logs.append([f'发现未定义变量！！！请确认: {missing_keys}', logging.WARNING])
    except UnicodeDecodeError as e:
        logs.append([f'警告：warning: {str(e)}', logging.WARNING])
    except Exception as e:
        logs.append([f'失败：error: {str(e)}', logging.ERROR])
    return matched_keys, missing_keys, status, replace_num, logs


def fix_global_csv(csv_path, var_fps_map):
    with open(csv_path, newline='', encoding='utf-8-sig') as fin, \
            tempfile.NamedTemporaryFile('w', newline='', encoding='utf-8-sig', delete=False) as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            key = row['变量键（KEY）']
            row['文件路径'] = '\n'.join(var_fps_map[key]) if key in var_fps_map else row['文件路径']
            writer.writerow(row)
    shutil.copyfile(fout.name, csv_path)


def dispose_controls(install_dir, replace_mode='字符串和控制符', check=True, dispose_fps=()):
    """
    处理中控类文件
    :param install_dir: 交付物目录路径
    :param replace_mode: 替换模式，字符串和控制符、仅控制符
    :param check: 是否检查
    :param dispose_fps: 需要处理的模版文件
    :return:
    """
    deploy_error_ret, script_error_ret = check_standard(install_dir)
    total_logs, file_logs = [], []
    template_paths = query_template_paths(install_dir) if check else dispose_fps
    all_vars, empty_idx_set, var_error_ret = load_data_from_csv(os.path.join(install_dir, 'controls/global-vars.csv'))
    vars_map, file_vars_map = dict(), dict()
    for i, value in enumerate(all_vars):
        if i in empty_idx_set: continue
        assert value[1] not in vars_map.keys(), '全局变量 {} 重复定义，请检查并更换变量名称！！！'.format(value[1])
        vars_map[value[1]] = value[4]
        var_files = value[5] and value[5].splitlines() or []
        var_files = len(var_files) == 1 and var_files[0].split(',') or var_files
        [file_vars_map.setdefault(os.path.join(install_dir, vf), set()).add(value[1]) for vf in var_files]
    all_missing_keys, all_matched_keys, all_defined_keys = set(), set(), set()
    # 替换成功的文件数量，替换成功的变量数量
    ok_file_num, all_replace_num, warn_fp, error_fp = 0, 0, [], []
    # 变量:文件路径 map
    var_fps_map = dict()
    for fp in template_paths:
        defined_keys = file_vars_map.get(fp, set())
        replace_ret = replace_placeholders_in_file(fp, vars_map, defined_keys, replace_mode, check)
        matched_keys, missing_keys, status, replace_num, replace_logs = replace_ret
        for l in replace_logs:
            if l[1] == logging.WARNING:
                warn_fp.append(fp)
            elif l[1] == logging.ERROR:
                error_fp.append(fp)
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
    error_fp and total_logs.append([f'处理以下模版文件时出现错误，请修复！  \n{error_fp}', logging.ERROR])
    return [deploy_error_ret, script_error_ret, var_error_ret], file_logs, total_logs, var_fps_map


def exec_replace(install_dir, replace_mode='字符串和控制符', check=False) -> Tuple[bool, List[Tuple[str, int]]]:
    """
    :param install_dir: 物料路径
    :param replace_mode: 字符串和控制符 or 仅控制符
    :param check: 是否检查模式
    :return: (脚本执行状态，日志及级别）
    """
    exist_error, logs = False, []
    error_log_titles = [['deploy-execution-plan.csv 检查失败：', logging.ERROR],
                        ['script-execution-plan.csv 检查失败：', logging.ERROR],
                        ['global-vars.csv 检查失败：', logging.ERROR]]
    try:
        error_rets, file_logs, total_logs, var_fps_map = dispose_controls(install_dir, replace_mode, True)
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
            dispose_controls(install_dir, replace_mode, False, dispose_fps)
        return True, [*logs, ['全局变量替换工具执行成功！', logging.INFO]]
    except Exception as e:
        return False, [*logs, [str(e), logging.ERROR], ['全局变量替换工具执行成功！', logging.INFO]]


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


def main(install_dir, replace_mode='字符串和控制符', check=False):
    log = set_logger()
    try:
        status, logs_with_level = exec_replace(install_dir, replace_mode, check)
        [log.log(l[1], l[0]) for l in logs_with_level]
    except Exception as e:
        log.exception(e)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='run replace script.')
    parser.add_argument('install_dir', nargs='?', default=get_real_app_dir(), help='交付物料包路径')
    parser.add_argument('--check', action='store_true', help='是否检查模式')
    parser.add_argument('--replace_mode', choices=['字符串和控制符', '仅控制符'], default='字符串和控制符',
                        help='变量替换模式，(字符串和控制符 or 仅控制符)')
    args = parser.parse_args()
    main(args.install_dir, replace_mode=args.replace_mode, check=args.check)

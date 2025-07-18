# -*- coding: utf-8 -*-

import os
import re
import csv
import sys
import logging
import argparse

CONTROL_COLUMNS_MAP = {'var': '序号 变量键（KEY） 变量描述 变量类型 填写示例 文件路径 填写说明',
                       'deploy': '步骤 资源类型 资源名称 命名空间 部署类型 YAML路径 镜像包名称 备注',
                       'script': '步骤 脚本路径 是否幂等 是否依赖 执行机类型 执行用户 K8S命名空间 负载资源名称 备注'}
# deploy-execution-plan.csv columns
DEPLOY_RESOURCE_TYPE = {'namespace': 0,
                        'configmap': 1, 'secret': 1, 'pvc': 1,
                        'deployment': 2, 'statefulset': 2, 'daemonset': 2, 'job': 2, 'cronjob': 2, 'service': 2,
                        'hpa': 3, 'vpa': 3,
                        'ingress': 4}
DEPLOY_NEED_IMAGE = {'deployment', 'statefulset', 'daemonset', 'job', 'cronjob'}
DEPLOY_TYPE = {'更新', '下线', '重启'}
# global-vars.csv columns
VAR_TYPE = {'字符串', '数值', '布尔'}
# script-execution-plan.csv columns
SCRIPT_COLUMNS = '步骤 脚本路径 是否幂等 是否依赖 执行机类型 执行用户 K8S命名空间 负载资源名称 备注'
CONTROL_TYPE_MAP = {'var': 'global-vars', 'deploy': 'deploy-execution-plan', 'script': 'script-execution-plan'}
# fomat：VAR_NAME 或 \x02VAR_NAME\x03
PATTERN = re.compile(r'(?:\\x02|\x02)(.*?)(?:\\x03|\x03)')


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


log = set_logger()


def check_standard(install_dir):
    assert os.path.exists(install_dir), '{} 部署包目录不存在，请输入正确路径！'.format(install_dir)
    if not os.path.exists(os.path.join(install_dir, 'controls/deploy-execution-plan.csv')):
        log.info('~~~~~~~~~~ 未发现【容器部署计划】deploy-execution-plan.csv，请确认本次发版是否不涉及容器部署！~~~~~~~~~~')
    else:
        load_data_from_csv(os.path.join(install_dir, 'controls/deploy-execution-plan.csv'), 'deploy')
        log.info(30 * '~' + '%s' + 30 * '~', ' 【容器部署计划】检查通过 ')
    if not os.path.exists(os.path.join(install_dir, 'controls/script-execution-plan.csv')):
        log.info('~~~~~~~~~~ 未发现【脚本执行计划】script-execution-plan.csv，请确认本次发版是否不涉及脚本执行！~~~~~~~~~~')
    else:
        load_data_from_csv(os.path.join(install_dir, 'controls/script-execution-plan.csv'), 'script')
        log.info(30 * '~' + '%s' + 30 * '~', ' 【脚本执行计划】检查通过 ')
    assert os.path.exists(os.path.join(install_dir, 'controls/global-vars.csv')), '未发现【全局变量清单】global-vars.csv，请确认！！！'


def query_template_filepaths(root_dir):
    """
    查找全部模版文件路径
    :param root_dir:
    :return:
    """
    filepaths = []

    def walk_files(template_dir):
        for subdir, _, files in os.walk(template_dir):
            for filename in files:
                filepath = os.path.join(subdir, filename)
                os.path.basename(filepath) != 'global-vars.csv' and filepaths.append(filepath)

    walk_files(os.path.join(root_dir, 'k8s-resources'))
    walk_files(os.path.join(root_dir, 'scripts'))
    walk_files(os.path.join(root_dir, 'controls'))
    return filepaths


def load_data_from_csv(filepath: str, control_type='var'):
    """
    读取全局变量字典
    :param control_type: var, deploy, script
    :param filepath:
    :return:
    """
    install_path = os.path.dirname(os.path.dirname(filepath))
    line_data, fmt = read_file_with_autoencoding(filepath, 'csv')
    assert len(line_data), '{} 文件格式有误，未发现列名！'.format(CONTROL_TYPE_MAP[control_type])
    assert ' '.join(line_data[0]) == CONTROL_COLUMNS_MAP[control_type], '{} 文件列名不匹配！列名应为 {}'.format(
        CONTROL_TYPE_MAP[control_type], CONTROL_COLUMNS_MAP[control_type])
    data_lines = line_data[1:]
    empty_idx_set = {
        i for i, row in enumerate(data_lines)
        if all(str(cell).strip() == '' or cell is None for cell in row)
    }
    deploy_seq_last = 0
    for idx, row in enumerate(data_lines):
        if idx in empty_idx_set:
            continue
        error_log = lambda x, y: '{} 第 {} 行 {} 列，【{}】 {}'.format(CONTROL_TYPE_MAP[control_type],
                                                                    idx + 1, x + 1, line_data[0][x], y)
        # VAR_COLUMNS = '序号 变量键（KEY） 变量描述 变量类型 填写示例 文件路径 填写说明'
        if control_type == 'var':
            for i in range(len(row[:4])):
                assert row[i], '{} 不存在，请处理！'.format(error_log(i, row[i]))
            assert row[3] in VAR_TYPE, '{} 不在支持变量类型范围内，请调整！'.format(error_log(3, row[3]))
            var_files = row[5] and row[5].strip().splitlines() or []
            var_files = len(var_files) == 1 and var_files[0].split(',') or var_files
            for var_file in var_files:
                assert os.path.exists(os.path.join(install_path, var_file)), \
                    '{} 相对路径不存在，如多个文件路径则使用换行分隔，请调整！'.format(error_log(5, var_file))
        # DEPLOY_COLUMNS = '步骤 资源类型 资源名称 命名空间 部署类型 YAML路径 镜像包名称 备注'
        elif control_type == 'deploy':
            for i in range(5):
                assert row[i], '{} 不存在，请处理！'.format(error_log(i, row[i]))
            assert row[1].lower() in DEPLOY_RESOURCE_TYPE.keys(), ('{} 不在支持资源类型范围内，请调整！'
                                                                   .format(error_log(1, row[1])))
            assert row[4] in DEPLOY_TYPE, '{} 不在支持部署类型范围内，请调整！'.format(error_log(4, row[4]))
            if '更新' == row[4]:
                assert DEPLOY_RESOURCE_TYPE[row[1].lower()] >= deploy_seq_last, \
                    ('{} 部署先后顺序有误，请根据资源类型调整部署顺序！'.format(error_log(1, row[1])))
                deploy_seq_last = DEPLOY_RESOURCE_TYPE[data_lines[idx][1].lower()]
                # yaml不存在
                assert row[5], '{} 不存在，请处理！'.format(error_log(5, row[5]))
                assert os.path.exists(os.path.join(install_path, row[5])), ('{} 相对路径不存在，请调整！'
                                                                            .format(error_log(5, row[5])))
                filename, ext = os.path.splitext(row[5])
                assert os.path.basename(filename).split('-')[-1].lower() == row[1].lower() \
                       and ext[2:] in {'aml', 'ml'}, '{} 不符合 YAML 命名规范，请调整！'.format(error_log(5, row[5]))
                # deployment，镜像包
                assert row[1].lower() not in DEPLOY_NEED_IMAGE or row[6], ('{} 不存在，请处理！'
                                                                           .format(error_log(6, row[6])))
        # SCRIPT_COLUMNS = '步骤 脚本路径 是否幂等 是否依赖 执行机类型 执行用户 K8S命名空间 负载资源名称 备注'
        elif control_type == 'script':
            for i in range(5):
                assert row[i], '{} 不存在，请处理！'.format(error_log(i, row[i]))
            assert os.path.exists(os.path.join(install_path, row[1])), ('{} 相对路径不存在，请调整！'
                                                                        .format(error_log(1, row[1])))
            assert row[2] in {'是', '否'}, '{} 请填写是或否，如需补充说明请写至备注中。'.format(error_log(2, row[2]))
            assert row[3] in {'是', '否'}, '{} 请填写是或否，如需补充说明请写至备注中。'.format(error_log(3, row[3]))
            assert row[4] in {'宿主机', '容器'}, ('{} 请填写宿主机或容器，如需补充说明请写至备注中。'
                                                  .format(error_log(4, row[4])))
        else:
            break
    return data_lines, empty_idx_set


def replace_placeholders(text, variables):
    missing_keys, matched_keys = set(), set()
    replace_num = 0

    def replacer(match):
        key = match.group(1)
        nonlocal replace_num
        if key in variables:
            replace_num += 1
            matched_keys.add(key)
            return variables[key]
        else:
            missing_keys.add(key)
            return match.group(0)

    return PATTERN.sub(replacer, text), matched_keys, missing_keys, replace_num


def read_file_with_autoencoding(filepath: str, file_type=None):
    fmt_list = ['utf-8-sig', 'gbk']
    for fmt in fmt_list:
        try:
            if file_type == 'csv':
                with open(filepath, 'r', newline='', encoding=fmt) as csvfile:
                    content = csv.reader(csvfile)
                    content = [row for row in content]
            else:
                with open(filepath, 'r', encoding=fmt) as f:
                    content = f.read()
            fmt != 'utf-8-sig' and log.info('！！！尝试 %s 加载模版成功，请调整该文件编码为 utf-8 ！', fmt)
            return content, fmt
        except UnicodeDecodeError as e:
            log.warning('尝试 %s 编码格式加载模版失败！%s, error: %s', fmt, filepath, e)
    raise Exception('文件编码不支持，模版文件无法正常打开！！！')


def replace_placeholders_in_file(template_path: str, variables: dict, defined_keys: set, check=False):
    """
    替换文件中的占位符
    :param template_path:
    :param variables:
    :param defined_keys:
    :param check:
    :return:
    """
    matched_keys, missing_keys, status = set(), set(), False
    try:
        content, fmt = read_file_with_autoencoding(template_path)
        replaced_text, matched_keys, missing_keys, replace_num = replace_placeholders(content, variables)
        defined_keys.difference_update(matched_keys)
        status = True
        if not check:
            with open(template_path, 'w', encoding='utf-8') as f:
                f.write(replaced_text)
        log.info('成功：%s, 替换位置 %d 个', template_path, replace_num)
        defined_keys and log.warning(' ##### %s 变量的【文件路径】⬆ 中未找到对应变量！请修改变量对应路径！！！', defined_keys)
        missing_keys and log.warning(' ##### 发现未定义变量！！！请确认: %s', missing_keys)
    except UnicodeDecodeError as e:
        log.error('失败：%s, 编码错误，error: %s', template_path, e)
    except Exception as e:
        log.error('失败：%s, error: %s', template_path, e)
    return matched_keys, missing_keys, status


def dispose_controls(install_dir, check):
    log.info('*' * 55 + '  执行开始  ' + '*' * 55)
    check_standard(install_dir)
    filepaths = query_template_filepaths(install_dir)
    all_vars, empty_idx_set = load_data_from_csv(os.path.join(install_dir, 'controls/global-vars.csv'))
    vars_map, file_vars_map = dict(), dict()
    for i, value in enumerate(all_vars):
        if i in empty_idx_set:
            continue
        assert value[1] not in vars_map.keys(), '全局变量 {} 重复定义，请修改台账！'.format(value[1])
        vars_map[value[1]] = value[4]
        var_files = value[5] and value[5].splitlines() or []
        var_files = len(var_files) == 1 and var_files[0].split(',') or var_files
        [file_vars_map.setdefault(os.path.join(install_dir, vf), set()).add(value[1]) for vf in var_files]
    all_missing_keys, all_matched_keys, all_defined_keys = set(), set(), set()
    ok_num = 0
    for fp in filepaths:
        defined_keys = file_vars_map.get(fp, set())
        matched_keys, missing_keys, status = replace_placeholders_in_file(fp, vars_map, defined_keys, check)
        all_matched_keys = all_matched_keys.union(matched_keys)
        all_missing_keys = all_missing_keys.union(missing_keys)
        all_defined_keys = all_defined_keys.union(defined_keys)
        ok_num += status

    unused_keys = vars_map.keys() - all_matched_keys
    log.info(30 * '~' + '%s' + 30 * '~', ' 【全局变量替换】执行结果 ')
    log.info('共计处理 %d 个模版文件，成功 %d 个， 失败 %d 个！', len(filepaths), ok_num, len(filepaths) - ok_num)
    log.info('全局变量表中共定义 %d 个变量，成功替换 %d 个，有 %d 个已定义未替换！有 %d 个未定义未替换！！！',
             len(vars_map.keys()), len(all_matched_keys), len(unused_keys), len(all_missing_keys))
    unused_keys and log.warning('%s 变量在全局变量表中定义，但未在部署模版中发现！请确认！', unused_keys)
    all_missing_keys and log.warning('%s 变量在部署模版中发现！但未在全局变量表中定义！请确认！', all_missing_keys)
    all_defined_keys and log.warning('%s 变量定义的【文件路径】中未找到对应变量！请修正变量对应路径！具体路径请往上找日志。',
                                     all_defined_keys)


def main(install_dir, check=False):
    try:
        dispose_controls(install_dir, check)
    except Exception as e:
        log.exception(e)


def get_real_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run install script.')
    parser.add_argument('install_dir', nargs='?', default=get_real_app_dir(), help='Path to the install directory')
    parser.add_argument('--check', action='store_true', help='Enable check mode')
    args = parser.parse_args()
    main(args.install_dir, args.check)

# -*- coding: utf-8 -*-
"""属地全局变量替换入口；替换和检查逻辑统一复用 vars_utils。"""

import argparse
import os
import sys
from pathlib import Path

# 支持从任意工作目录使用 python /path/to/src/tools/replace_vars.py 执行。
if __package__ in {None, ''} and not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import vars_utils


def get_real_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main(install_dir, replace_mode='字符串和控制符', check=False, old_image_prefix=None, new_image_prefix=None):
    log = vars_utils.set_logger()
    status, logs_with_level = vars_utils.exec_replace(
        install_dir, replace_mode, check, old_image_prefix, new_image_prefix, strict=False,
    )
    for index, (message, level) in enumerate(logs_with_level):
        if index == len(logs_with_level) - 1:
            message += '（推荐访问 http://10.19.44.77:44666/deployment_config 编写全局变量。）'
        log.log(level, message)
    return 0 if status else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='属地全局变量替换：检查变量、文件引用和替换后语法，不校验发布规范。',
    )
    parser.add_argument('install_dir', nargs='?', default=get_real_app_dir(), help='交付物料包路径')
    parser.add_argument('--check', action='store_true', help='仅检查，不修改物料文件')
    parser.add_argument('--replace_mode', choices=list(vars_utils.PATTERN_MAP), default='字符串和控制符',
                        help='变量替换模式，(字符串和控制符 or 仅控制符)')
    parser.add_argument('--old_image_prefix', default=vars_utils.DEFAULT_IMAGE_PREFIX, help='待匹配的镜像前缀')
    parser.add_argument('--new_image_prefix', default=vars_utils.DEFAULT_IMAGE_PREFIX, help='待替换的镜像前缀')
    args = parser.parse_args()
    sys.exit(main(args.install_dir, replace_mode=args.replace_mode, check=args.check,
                  old_image_prefix=args.old_image_prefix, new_image_prefix=args.new_image_prefix))

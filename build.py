# -*- coding: utf-8 -*-
# Created By: Virgil Dupras
# Created On: 2009-12-30
# Copyright 2010 Hardcoded Software (http://www.hardcoded.net)
# 
# This software is licensed under the "BSD" License as described in the "LICENSE" file, 
# which should be included with this package. The terms are also available at 
# http://www.hardcoded.net/licenses/bsd_license

import os
import os.path as op
import shutil
import json

from setuptools import setup
from distutils.extension import Extension

from hscommon import sphinxgen
from hscommon.build import (add_to_pythonpath, print_and_do, copy_packages, ensure_empty_folder)

def build_cocoa(edition, dev):
    print("Building dg_cocoa.plugin")
    if not dev:
        specific_packages = {
            'se': ['core_se'],
            'me': ['core_me'],
            'pe': ['core_pe'],
        }[edition]
        copy_packages(['core', 'hscommon'] + specific_packages, 'build')
    cocoa_project_path = 'cocoa/{0}'.format(edition)
    shutil.copy(op.join(cocoa_project_path, 'dg_cocoa.py'), 'build')
    os.chdir('build')
    script_args = ['py2app', '-A'] if dev else ['py2app']
    setup(
        script_args = script_args,
        plugin = ['dg_cocoa.py'],
        setup_requires = ['py2app'],
    )
    os.chdir('..')
    pluginpath = op.join(cocoa_project_path, 'dg_cocoa.plugin')
    if op.exists(pluginpath):
        shutil.rmtree(pluginpath)
    shutil.move('build/dist/dg_cocoa.plugin', pluginpath)
    if dev:
        # In alias mode, the tweakings we do to the pythonpath aren't counted in. We have to
        # manually put a .pth in the plugin
        pthpath = op.join(pluginpath, 'Contents/Resources/dev.pth')
        open(pthpath, 'w').write(op.abspath('.'))
    os.chdir(cocoa_project_path)
    print("Building the XCode project")
    args = []
    if dev:
        args.append('-configuration dev')
    else:
        args.append('-configuration release')
    args = ' '.join(args)
    os.system('xcodebuild {0}'.format(args))
    os.chdir('../..')
    print("Creating the run.py file")
    subfolder = 'dev' if dev else 'release'
    app_path = {
        'se': 'cocoa/se/build/{0}/dupeGuru.app',
        'me': 'cocoa/me/build/{0}/dupeGuru\\ ME.app',
        'pe': 'cocoa/pe/build/{0}/dupeGuru\\ PE.app',
    }[edition].format(subfolder)
    tmpl = open('run_template_cocoa.py', 'rt').read()
    run_contents = tmpl.replace('{{app_path}}', app_path)
    open('run.py', 'wt').write(run_contents)

def build_qt(edition, dev):
    print("Building Qt stuff")
    print_and_do("pyrcc4 -py3 {0} > {1}".format(op.join('qt', 'base', 'dg.qrc'), op.join('qt', 'base', 'dg_rc.py')))
    print("Creating the run.py file")
    tmpl = open('run_template_qt.py', 'rt').read()
    run_contents = tmpl.replace('{{edition}}', edition)
    open('run.py', 'wt').write(run_contents)

def build_help(edition, ui, dev):
    print("Generating Help")
    current_path = op.abspath('.')
    help_basepath = op.join(current_path, 'help', 'en')
    help_buildpath = op.join(current_path, 'build', 'sphinx_src'.format(edition))
    help_destpath = op.join(current_path, 'build', 'help'.format(edition))
    changelog_path = op.join(current_path, 'help', 'changelog_{}'.format(edition))
    tixurl = "https://hardcoded.lighthouseapp.com/projects/31699-dupeguru/tickets/{0}"
    appname = {'se': 'dupeGuru', 'me': 'dupeGuru Music Edition', 'pe': 'dupeGuru Picture Edition'}[edition]
    homepage = 'http://www.hardcoded.net/dupeguru{}/'.format('_' + edition if edition != 'se' else '')
    confrepl = {'edition': edition, 'appname': appname, 'homepage': homepage}
    sphinxgen.gen(help_basepath, help_buildpath, help_destpath, changelog_path, tixurl, confrepl)

def build_pe_modules(ui):
    def move(src, dst):
        if not op.exists(src):
            return
        if op.exists(dst):
            os.remove(dst)
        print('Moving %s --> %s' % (src, dst))
        os.rename(src, dst)
    
    print("Building PE Modules")
    exts = [
        Extension("_block", [op.join('core_pe', 'modules', 'block.c'), op.join('core_pe', 'modules', 'common.c')]),
        Extension("_cache", [op.join('core_pe', 'modules', 'cache.c'), op.join('core_pe', 'modules', 'common.c')]),
    ]
    if ui == 'qt':
        exts.append(Extension("_block_qt", [op.join('qt', 'pe', 'modules', 'block.c')]))
    elif ui == 'cocoa':
        exts.append(Extension(
            "_block_osx", [op.join('core_pe', 'modules', 'block_osx.m'), op.join('core_pe', 'modules', 'common.c')],
            extra_link_args=[
                "-framework", "CoreFoundation",
                "-framework", "Foundation",
                "-framework", "ApplicationServices",]
        ))
    setup(
        script_args = ['build_ext', '--inplace'],
        ext_modules = exts,
    )
    move('_block.so', op.join('core_pe', '_block.so'))
    move('_block.pyd', op.join('core_pe', '_block.pyd'))
    move('_block_osx.so', op.join('core_pe', '_block_osx.so'))
    move('_cache.so', op.join('core_pe', '_cache.so'))
    move('_cache.pyd', op.join('core_pe', '_cache.pyd'))
    move('_block_qt.so', op.join('qt', 'pe', '_block_qt.so'))
    move('_block_qt.pyd', op.join('qt', 'pe', '_block_qt.pyd'))

def main():
    conf = json.load(open('conf.json'))
    edition = conf['edition']
    ui = conf['ui']
    dev = conf['dev']
    print("Building dupeGuru {0} with UI {1}".format(edition.upper(), ui))
    ensure_empty_folder('build')
    if dev:
        print("Building in Dev mode")
    add_to_pythonpath('.')
    build_help(edition, ui, dev)
    print("Building dupeGuru")
    if edition == 'pe':
        build_pe_modules(ui)
    if ui == 'cocoa':
        build_cocoa(edition, dev)
    elif ui == 'qt':
        build_qt(edition, dev)

if __name__ == '__main__':
    main()

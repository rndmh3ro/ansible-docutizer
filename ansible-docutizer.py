#!/usr/bin/env python2
# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import codecs
import sys
import os

from ansible import constants as C
from ansible.cli import CLI
from ansible.errors import AnsibleError, AnsibleOptionsError, AnsibleUndefinedVariable, AnsibleParserError
from ansible.executor.playbook_executor import PlaybookExecutor
from ansible.playbook.block import Block
from ansible.playbook.play_context import PlayContext
from ansible.module_utils._text import to_bytes, to_text
from ansible.template import Templar, AnsibleEnvironment
from ansible.utils.listify import listify_lookup_plugin_terms
from ansible.plugins.loader import action_loader, connection_loader, filter_loader, lookup_loader, module_loader, test_loader
from jinja2 import FileSystemLoader
from jinja2.exceptions import TemplateNotFound, TemplateError


try:
    from __main__ import display
except ImportError:
    from ansible.utils.display import Display
    display = Display()


class SharedPluginLoaderObj:
    def __init__(self):
        self.action_loader = action_loader
        self.connection_loader = connection_loader
        self.filter_loader = filter_loader
        self.test_loader = test_loader
        self.lookup_loader = lookup_loader
        self.module_loader = module_loader


class PlaybookDocutizer(CLI):

    exclude_actions = ['meta', 'setup']

    def parse(self):
        parser = CLI.base_parser(
            usage="%prog [options] playbook.yml",
            desc="Creates documentation from Ansible playbooks.",
        )

        parser.add_option('-i', '--inventory', dest='inventory', action="append",
                          help='specify inventory host path or comma separated host list')
        parser.add_option('-l', '--limit', default=C.DEFAULT_SUBSET, dest='subset',
                          help='further limit selected hosts to an additional pattern')
        parser.add_option('--output', '-o', dest='output',
                          help='output file', default='output.txt')
        parser.add_option('--template-path', dest='template_path',
                          help='path to template files', default='templates')
        parser.add_option('--template-master', dest='template_master',
                          help='path to template master file', default='master.j2')
        parser.add_option('--showdetails', dest='showdetails', action='store_true',
                          help='show JSON details for task')
        parser.add_option('--showtasks', dest='showtasks', action='store_true',
                          help='show tasks in table of contents')

        self.parser = parser
        super(PlaybookDocutizer, self).parse()

        if len(self.args) == 0:
            raise AnsibleOptionsError('You must specify a playbook file to run')

        display.verbosity = self.options.verbosity
        self.options.vault_ids = []
        self.options.vault_password_files = []
        self.options.ask_vault_pass = False
        # NOTE(iwalker): need to set liststasks to True so pbex.run() does not actually run the playbook
        self.options.listtasks = True
        self.options.listhosts = False
        self.options.listtags = False
        self.options.syntax = False
        self.options.become = False
        self.options.become_method = 'sudo'
        self.options.become_user = 'root'
        self.options.check = False
        self.options.diff = False

        if self.options.showdetails:
            display.warning('--showdetails option may expose passwords and other secure data!')


    def run(self):
        super(PlaybookDocutizer, self).run()

        for playbook in self.args:
            if not os.path.exists(playbook):
                raise AnsibleError("the playbook: %s could not be found" % playbook)
            if not (os.path.isfile(playbook) or stat.S_ISFIFO(os.stat(playbook).st_mode)):
                raise AnsibleError("the playbook: %s does not appear to be a file" % playbook)

        self._shared_loader_obj = SharedPluginLoaderObj()

        self._loader, self._inventory, self._variable_manager = self._play_prereqs(self.options)

        pbex = PlaybookExecutor(playbooks=self.args, inventory=self._inventory, variable_manager=self._variable_manager, loader=self._loader, options=self.options, passwords={})
        results = pbex.run()

        if isinstance(results, list):
            for p in results:
                plays = []
                for idx, play in enumerate(p['plays']):
                    display.display('Processing play %d: %s'%(idx+1, play.name))

                    if play._included_path is not None:
                        self._loader.set_basedir(play._included_path)
                    else:
                        pb_dir = os.path.realpath(os.path.dirname(p['playbook']))
                        self._loader.set_basedir(pb_dir)

                    hosts = CLI.get_host_list(self._inventory, self.options.subset)

                    host = hosts[0]
                    display.v('Processing against host: %s'%(host.get_name()))

                    self._all_vars = self._variable_manager.get_vars(play=play, host=host)
                    play_context = PlayContext(play=play, options=self.options)

                    processed_blocks = []
                    for block in play.compile():
                        block = block.filter_tagged_tasks(play_context, self._all_vars)
                        if not block.has_tasks():
                            continue
                        processed_blocks.append(self._process_block(block))

                    tasks = []
                    for block in processed_blocks:
                        if len(block) == 0:
                            continue
                        for task in block:
                            tasks.append(task)

                    processed_handlers = []
                    for block in play.compile_roles_handlers():
                        processed_handlers.extend(self._process_block(block))

                    play_info = {
                        'filename':p['playbook'],
                        'roles':play.roles,
                        'hosts':hosts,
                        'name':play.name,
                        'roles':play.roles,
                        'tasks':tasks,
                        'handlers':processed_handlers,
                        'become':play.become,
                        'remote_user':play.remote_user,
                    }

                    plays.append(play_info)

                env = AnsibleEnvironment(trim_blocks=True,
                                         extensions=['jinja2.ext.loopcontrols'],
                                         loader=FileSystemLoader(self.options.template_path))

                display.display('Rendering template containing %d plays'%(len(plays)))
                template = env.get_template(self.options.template_master)
                output = template.render(plays=plays,
                                         options=self.options)

                display.display('Saving output to %s'%(self.options.output))
                with codecs.open(self.options.output, mode='w', encoding='utf-8') as f:
                    f.write(output)


    def _process_block(self, b):
        results = []
        for task in b.block:
            if isinstance(task, Block):
                results.extend(self._process_block(task))
            else:
                if task.action in self.exclude_actions:
                    continue
                results.append(self._process_task(task))
        return results


    def _process_task(self, t):
        task_data = self._task_data_for_template(t)
        task_data['eval'] = self._post_validate_task(t)
        task_data['loop_eval'] = self._process_task_loops(t)
        return task_data


    def _post_validate_task(self, task):
        if task.loop_with or task.loop:
            return None

        task_vars = self._variable_manager.get_vars(task=task)
        templar = Templar(loader=self._loader, shared_loader_obj=self._shared_loader_obj, variables=self._all_vars)
        try:
            tmp_task = task.copy(exclude_parent=True, exclude_tasks=True)
            tmp_task._parent = task._parent
            tmp_task.post_validate(templar=templar)
        except AnsibleParserError as e:
            display.warning(e)
            return None

        if tmp_task.args == task.args:
            return None

        return self._task_data_for_template(tmp_task)


    def _process_task_loops(self, task):
        results = []
        if task.loop_with or task.loop:
            task_vars = self._variable_manager.get_vars(task=task)
            templar = Templar(loader=self._loader, shared_loader_obj=self._shared_loader_obj, variables=self._all_vars)
            items = self._get_loop_items(task, templar, task_vars)
            if items:
                for item in items:
                    try:
                        task_vars['item'] = item
                        templar.set_available_variables(task_vars)
                        tmp_task = task.copy(exclude_parent=True, exclude_tasks=True)
                        tmp_task._parent = task._parent
                        tmp_task.post_validate(templar=templar)
                        results.append(self._task_data_for_template(tmp_task))
                        del task_vars['item']
                    except AnsibleParserError as e:
                        display.warning('%s caused an AnsibleParseError; ignoring'%(tmp_task))
                        results.clear()
        return results


    def _get_loop_items(self, task, templar, task_vars):
        items = None
        if task.loop_with:
            if task.loop_with in self._shared_loader_obj.lookup_loader:
                try:
                    loop_terms = listify_lookup_plugin_terms(terms=task.loop, templar=templar, loader=self._loader, fail_on_undefined=True, convert_bare=False)
                    mylookup = self._shared_loader_obj.lookup_loader.get(task.loop_with, loader=self._loader, templar=templar)
                    items = mylookup.run(terms=loop_terms, variables=task_vars, wantlist=True)
                except AnsibleUndefinedVariable as e:
                    display.warning('Processing %s caused an AnsibleUndefinedVariable error: %s'%(task, e))
        elif task.loop:
            items = templar.template(task.loop)
        return items


    def _task_data_for_template(self, task):
        result = {'name':task.name,
                  'role':task._role,
                  'has_loop':(task.loop or task.loop_with),
                  'loop':task.loop,
                  'loop_with':task.loop_with,
                  'action':task.action,
                  'args':task.args,
                  'when':task.when,
                  'notify':task.notify,
                  'register':task.register,
                  'validate':str(task.validate),
                  'ds':task.get_ds()}
        return result


if __name__ == "__main__":
    try:
        cli = PlaybookDocutizer([to_text(a, errors='surrogate_or_strict') for a in sys.argv])
        cli.parse()
        exit_code = cli.run()
    except AnsibleOptionsError as e:
        cli.parser.print_help()
        display.error(to_text(e), wrap_text=False)
        exit_code = 5
    except AnsibleParserError as e:
        display.error(to_text(e), wrap_text=False)
        exit_code = 4
    except TemplateError as e:
        display.error(to_text(e), wrap_text=False)
        exit_code = 6
    except KeyboardInterrupt:
        display.error('User interrupted execution')
        exit_code = 99
    sys.exit(exit_code)

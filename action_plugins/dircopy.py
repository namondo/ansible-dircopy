from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from ansible.errors import AnsibleError
from ansible.module_utils._text import to_bytes, to_native, to_text
from ansible.plugins.action import ActionBase
import os
import subprocess
from time import time
from datetime import datetime as dt
import errno


def check_file_mode(mode):
    if len(mode) not in (3, 4) or not mode.isdigit():
        return False, "Invalid mode"
    if len(mode) == 4:
        sticky = mode[:1]
        if sticky not in ('0', '1'):
            return False
    else:
        mode = "0" + mode
    valid_modes = ('0', '1', '2', '4', '6', '7')
    valid = all(m in valid_modes for m in mode)
    return mode if valid else None


def timestamp():
    return dt.fromtimestamp(int(time())).strftime('%Y-%m-%d_%H-%M-%S')

TARFILE = "_dircopy_%s.tar" % timestamp()


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super(ActionModule, self).run(tmp, task_vars)

        source = self._task.args.get('src', None)
        dest = self._task.args.get('dest', None)
        owner = self._task.args.get('owner', "")
        group = self._task.args.get('group', "")
        mode = self._task.args.get('mode', "")
        # backup = self._task.args.get('backup', None)
        identical = self._task.args.get('identical', None)
        x4dirs = self._task.args.get('x4dirs', None)
        remote_tmp = self._task.args.get('remote_tmp', None)
        verbose = self._task.args.get('verbose', None)
        local_tmp = self._task.args.get('local_tmp', "/tmp/")

        remote_user = task_vars.get('ansible_ssh_user') or self._play_context.remote_user

        # Parameter tests
        source = os.path.abspath(source)
        if source == "/":
            result["failed"] = True
            result["msg"] = "You don't want to transfer the whole fs, do you?"
            return result
        if not os.path.isdir(source):
            result["failed"] = True
            result["msg"] = "src must be a directory"
            return result
        try:
            src = self._find_needle('files', source)
        except AnsibleError as e:
            result['failed'] = True
            result['msg'] = to_native(e)
            return result

        if mode:
            mode = check_file_mode(mode)
            if not mode:
                result["failed"] = True
                result["msg"] = "Invalid mode: %s" % self._task.args.get('mode')
                return result
        #################

        if not remote_tmp:
            tmp = self._make_tmp_path(remote_user)
            self._cleanup_remote_tmp = True

        tmpfile = os.path.join(tmp, TARFILE)
        local_tmpfile = os.path.join(local_tmp, TARFILE)
        src_dirname = src.split("/")[-2] if src[-1] == "/" else src.split("/")[-1]
        # mode_attr = "--mode {}".format(mode) if mode else ""
        cmd = "tar --transform 's/^%s//g' -cf %s %s/*" % (src_dirname, local_tmpfile, src_dirname)
        try:
            _ = subprocess.call(cmd, cwd=os.path.join(src, ".."), shell=True, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError, e:
            result['failed'] = True
            result['msg'] = to_native(e)
            return result

        remote_user = task_vars.get('ansible_ssh_user') or self._play_context.remote_user
        copy_module_args = self._task.args.copy()
        xfered = self._transfer_file(local_tmpfile, tmpfile)
        self._fixup_perms2((tmp, xfered), remote_user)
        try:
            os.remove(local_tmpfile)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise e

        copy_module_args.update(
            dict(
                src=xfered,
                dest=tmpfile,
                original_basename=TARFILE,
            ),
        )

        res = self._execute_module(
                module_name='copy',
                module_args=copy_module_args,
                task_vars=task_vars,
                tmp=tmp,
                delete_remote_tmp=False,
                persist_files=True
            )


        res.pop("invocation")

        if not owner:
            owner = remote_user

        module_args = dict(dest=dest, src=src, owner=str(owner), group=str(group), mode=mode, identical=identical,
                           verbose=verbose, x4dirs=x4dirs, _tmpfile=xfered,
                           # remote_tmp=remote_tmp,
                           # backup=backup
                           )

        module_res = self._execute_module(
            module_name='dircopy',
            module_args=module_args,
            task_vars=task_vars,
            tmp=remote_tmp,
        )

        result.update(module_res)
        return result

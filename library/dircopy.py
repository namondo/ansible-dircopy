#!/usr/bin/env python
# -*- coding: utf-8 -*-

DOCUMENTATION = '''
---
module: dircopy
short_description: copies directories recursively. Intended to bypass the slowness problem with Ansible copy module.
description:
    - Using the C tar create one file to transfer it to the target host(s) (using Ansible built-in copy module) 
      and to check/update the target directory. 
options:
  src:
    description:
      - Directory on the source host that will be copied to the destination; The path can be absolute or relative.
    required: true
  dest:
    description:
      - Directory on the destination host that will be synchronized from the source; 
        The path can be absolute or relative.
    required: true
  mode:
    description: 
      - permissions of the target, after execution
    type: string
    sample: "0644"
    required: false
      identical:
    description:
      - Delete files in dest that don't exist (after transfer, not before) in the src path.
    choices: [ 'yes', 'no' ]
    default: 'no'
    required: false
  owner:
    description:
      - target ownership 
    default: the value of the archive option
    required: false
  group:
    description:
      - target group membership
    required: false
  chdir: 
    description:
      - set execution flag additionally to owner or group rights (in case owner/group/others have any right on target) 
  verbose:
    description:
      - module exits with detailed information of updates, removals, etc. 
  
notes:
   - tar must be installed on both the local and remote host.
   - The module does not preserve the file ownership an permissions (you can set or it defaults to the Asible user 
     and the target's umask) 
   - The source cannot be "/"

author: "T. Czecher (ct@index.hu)"
'''

EXAMPLES = '''
# Copy of src on the control machine to dest on the remote hosts
dircopy: src=some/relative/path dest=/some/absolute/path

# Create an exact copy (delete files not in src)
dircopy:
    src: /tmp/helloworld
    dest: /var/www/helloworld
    identical: yes
'''

from ansible.module_utils.basic import *
import os
import pwd
import grp
import subprocess
from itertools import chain
from operator import itemgetter


def umask2mode(umask):
    # TODO:
    perms = [i.split("=")[1] for i in umask.split(",")]
    perms = ["-" if not i else i for i in perms]
    to_num = lambda x: x.replace("r", "4").replace("w", "2").replace("x", "1").replace("-", "0")
    mode_num = lambda x: sum([int(i) for i in to_num(x)])
    mode_string = "".join([str(mode_num(i)) for i in perms])
    return mode_string


class TarFile(object):
    def __init__(self, path, ansible_module):
        self.tarfile = path
        self.module = ansible_module
        self.check_mode = self.module.check_mode

    @staticmethod
    def _parse_tar_out(tar_out):
        missing_files = set()
        for line in tar_out.split("\n"):
            if not line.strip():
                continue
            if "Cannot stat" in line:
                missing_files.add(line.split(":")[1].strip())
        return missing_files

    def _runner(self, cmd):
        (rc, stdout, error) = self.module.run_command(cmd, use_unsafe_shell=False)

        if rc not in (0, 1):
            self.module.fail_json(change=False, msg=error)
        else:
            return stdout + error

    def list(self):
        stdout = self._runner("tar -tvf %s" % self.tarfile)
        if stdout:
            lines = filter(None, [line for line in stdout.split("\n")])
            dirs = [" ".join(line.split()[5:]) for line in lines if line.split()[0][:1] == "d"]
            dirs = set(self._remove_leading_slash(dirs))
            paths = set(self._remove_leading_slash([" ".join(line.split()[5:]) for line in lines]))
            files = paths - dirs
            return files, dirs
        return None, None

    @staticmethod
    def _remove_leading_slash(a_list):
        for i, item in enumerate(a_list):
            if item.startswith("/"):
                a_list[i] = item[1:]
        return a_list

    @staticmethod
    def _add_leading_slash(a_list):
        for i, item in enumerate(a_list):
            item = item.strip()
            if not item.startswith("/"):
                a_list[i] = "/" + item
        return a_list

    def untar(self, target):
        if self.check_mode:
            return
        command = "tar --preserve-permissions -xf %s -C %s" % (self.tarfile, target)
        self._runner(command)

    def compare(self, target):
        command = "tar --compare --file %s -C %s" % (self.tarfile, target)
        tar_out = self._runner(command)
        if tar_out:
            return self._parse_tar_out(tar_out.strip())
        return None

    def update(self, target, files2update):
        if self.check_mode:
            return
        files2update = self._add_leading_slash(list(files2update))
        for d in files2update:
            command = ['tar', '--extract', '--preserve-permissions', '--file', self.tarfile, '-C', target, d]
            _ = self._runner(command)


class File(object):
    def __init__(self, path):
        path = os.path.abspath(path)
        self.path = path if os.path.exists(path) else None
        self.uid, self.gid, self.mode = self.get_rights()

    def get_rights(self):
        if self.path:
            st = os.stat(self.path)
            mode = str(oct(st.st_mode))[-4:] if self.path else None
            return st.st_uid, st.st_gid, mode
        else:
            return None, None, None

    def set_owner(self, uid, gid):
        os.chown(self.path, uid, gid)

    def set_mode(self, mode):
        os.chmod(self.path, int(mode, 8))


def check_permissions(dest, uid, gid, perms, x4dirs, check_mode):
    details = dict()
    dir_perms = perms_with_exec(perms) if x4dirs else perms
    dest_files, dest_dirs = get_files(dest)
    files = [File(os.path.abspath(f)) for f in dest_files]
    files = [f for f in files if f.path]
    dirs = [File(d) for d in dest_dirs]
    dirs = [d for d in dirs if d.path]
    files_ownership_files2update = [f for f in files if (f.uid != uid)] + [f for f in files if (f.gid != gid)]
    dirs_ownership_files2update = [d for d in dirs if (d.uid != uid or d.gid != gid)]
    files_mode_files2update = [f for f in files if f.mode != perms]
    dirs_mode_files2update = [d for d in files if d.mode != dir_perms]
    if dirs_ownership_files2update or files_ownership_files2update:
        details["ownership"] = set([f.path for f in files_ownership_files2update] +
                                   [d.path for d in dirs_ownership_files2update])
        if not check_mode:
            [f.set_owner(uid, gid) for f in chain(dirs_ownership_files2update, files_ownership_files2update)]
    else:
        details["ownership"] = None
    if files_mode_files2update or dirs_mode_files2update:
        details["mode"] = set([f.path for f in files_mode_files2update] +
                              [d.path for d in dirs_mode_files2update])
        if not check_mode:
            set([f.set_mode(perms) for f in files_mode_files2update])
            set([d.set_mode(perms) for d in dirs_mode_files2update])
    else:
        details["mode"] = None
    return details


def get_files(target):
    files = set()
    dirs = list()
    for path, sub_dirs, _files in os.walk(target):
        dirs.append(path)
        for name in _files:
            files.add(os.path.abspath(os.path.join(path, name)))
    # Correct path endings
    for i, d in enumerate(dirs):
        dirs[i] = os.path.abspath(d)
    return files, set(dirs)


def perms_with_exec(mode_string):
    sticky_bit = mode_string[:1] if len(mode_string) == 4 else ""
    modes = [m for m in mode_string[-3:]]
    new_mode_string = sticky_bit
    for m in modes:
        mode = int(m)
        if mode % 2 == 0:
            mode = mode + 1 if mode else 0
        new_mode_string += str(mode)
    return new_mode_string


def make_identical(target, tarfile):
    (files_in_tar, dirs_in_tar) = tarfile.list()
    files_in_tar = set([os.path.abspath(target + f) for f in files_in_tar])
    dirs_in_tar = set([os.path.abspath(target + f) for f in dirs_in_tar])
    (target_files, target_dirs) = get_files(target)
    spare_files = target_files - files_in_tar
    spare_dirs = target_dirs - dirs_in_tar - set([os.path.abspath(target)])
    return spare_files, spare_dirs


def remove_spares(files, dirs):
    for f in files:
        os.remove(os.path.join(f))
    dirs_with_path_length = zip(list(dirs), [len(d.split("/")) for d in dirs])
    dirs_sorted_by_path = sorted(dirs_with_path_length, key=itemgetter(1), reverse=True)
    for d in dirs_sorted_by_path:
        try:
            os.rmdir(d[0])
        except OSError, e:
            return e
    return None


def main():
    argument_spec = {
        "src": {"required": True},
        "dest": {"required": True},
        "owner": {"required": False},
        "group": {"required": False},
        "mode": {"required": False},
        "identical": dict(default=False, aliases=['delete'], type='bool'),
        # TODO:
        # "backup": dict(default=False, type='bool'),
        "verbose": dict(default=True, type='bool'),
        "remote_tmp": dict(default="/tmp"),
        "chdir": dict(default=False, aliases=['x4dirs'], type='bool'),
        "_tmpfile": {"required": False},
    }

    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)
    check_mode = module.check_mode
    params = module.params

    src = params["src"]
    dest = params["dest"]
    if dest[-1:] != "/":
        dest += "/"
    owner = params["owner"]
    group = params["group"]
    mode = params["mode"]
    identical = params["identical"]
    # backup = params["backup"]
    verbose = params["verbose"]
    x4dirs = params["chdir"]
    tmpdir = params["remote_tmp"]
    tmpfile = os.path.join(tmpdir, params["_tmpfile"])

    if owner:
        if owner.isdigit():
            uid = int(owner)
        else:
            try:
                uid = pwd.getpwnam(owner).pw_uid
            except KeyError:
                module.exit_json(failed=True, changed=False, msg="No such a user: %s" % owner)
    if group:
        if group.isdigit():
            gid = int(group)
        else:
            try:
                gid = grp.getgrnam(group).gr_gid
            except KeyError:
                module.exit_json(failed=True, changed=False, msg="No such a group: %s" % group)
    else:
        gid = pwd.getpwnam(owner).pw_gid

    if not mode:
        umask = subprocess.check_output(["umask", "-S"])
        mode = umask2mode(umask.strip())

    failed = False
    changed = False
    files2update = None
    removed = False
    msg = list()

    tarfile = TarFile(path=tmpfile, ansible_module=module)

    if os.path.exists(dest):
        exit_cmd = "module.exit_json(failed=failed, changed=changed, msg=dict(enumerate(msg))"
        _msg = "differ(s)" if check_mode else "updated"
        if os.path.isdir(dest):
            files2update = tarfile.compare(target=dest)
            if files2update:
                changed = True
                tarfile.update(target=dest, files2update=files2update)
                msg.append("%s file(s) %s " % (len(files2update), _msg))
                if verbose:
                    files2update = [os.path.join(dest, d) for d in files2update]
                    if check_mode:
                        exit_cmd += ", files_to_update=list(files2update)"
                    else:
                        exit_cmd += ", updated_files=list(files2update)"

        else:
            module.fail_json(changed=False, result=False, msg="Destination (%s) is not a directory" % dest)
        if identical:
            spare_files, spare_dirs = make_identical(target=dest, tarfile=tarfile)
            if spare_files or spare_dirs:
                failed2remove = remove_spares(files=spare_files, dirs=spare_dirs)
                if failed2remove:
                    module.exit_json(failed=True, changed=False, msg=str(failed2remove))
            removed = spare_files | spare_dirs
            if removed:
                changed = True
                _msg = "would be removed" if check_mode else "removed"
                msg.append("%s file(s) and %s dir(s) %s" % (len(spare_files), len(spare_dirs), _msg))
                if verbose:
                    if check_mode:
                        exit_cmd += ", would_be_removed=list(removed)"
                    else:
                        exit_cmd += ", removed=list(removed)"

        changes = check_permissions(dest=dest, uid=uid, gid=gid, perms=mode, x4dirs=x4dirs, check_mode=check_mode)
        if changes['mode']:
            changed = True
            if verbose:
                permission_differ = changes['mode'] - set(files2update)
                msg.append("%s file's mode(s) %s" % (len(permission_differ), _msg))
                if check_mode:
                    perms_msg = [f + " mode(s) differ." for f in permission_differ]
                else:
                    perms_msg = [f + " mode updated." for f in permission_differ]

                exit_cmd += ", mode=perms_msg"
        if changes['ownership']:
            changed = True
            if verbose:
                ownership_differ = changes['ownership'] - set(files2update)
                msg.append("%s permission(s) %s" % (len(ownership_differ), _msg))
                if check_mode:
                    owner_msg = [f + " ownership (owner/group) differ." for f in ownership_differ]
                else:
                    owner_msg = [f + " owner/group updated." for f in ownership_differ]

                exit_cmd += ", ownership=owner_msg"
        if not changed:
            msg = ["No update needed."]
        exit_cmd += ")"
        eval(exit_cmd)

    # Target doesn't exist -> untar
    os.mkdir(dest)
    tarfile.untar(target=dest)
    check_permissions(dest=dest, uid=uid, gid=gid, perms=mode, x4dirs=x4dirs, check_mode=check_mode)
    changed = True
    msg = "%s copied to %s " % (src, dest)

    updated = True
    if verbose:
        updated = files2update
    module.exit_json(failed=False, changed=changed, msg=msg)

if __name__ == '__main__':
    main()

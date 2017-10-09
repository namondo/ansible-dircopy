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
        The path should absolute. If dest doesn't exists it will be created.
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
    default: the Ansible user
    required: false
  group:
    description:
      - target group membership
    default: the Ansible user's group
    required: false
  gzip:
    description:
      - gzip the directory on transfer (applicable only on directory copying)
    default: False
  specialx: 
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
    
dircopy: src=/tmp/test.tgz dest=/tmp/test
'''

from ansible.module_utils.basic import *
import os
import pwd
import grp
import subprocess
from operator import itemgetter


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

    def size(self):
        return os.stat(self.path).st_size if self.path else None

    def set_owner(self, uid, gid):
        os.chown(self.path, uid, gid)

    def set_mode(self, mode):
        os.chmod(self.path, int(mode, 8))

    def get_owner_name(self):
        return pwd.getpwuid(os.stat(self.path).st_uid).pw_name

    def get_group_name(self):
        return grp.getgrgid(os.stat(self.path).st_gid).gr_name


class TarFile(object):
    def __init__(self, path, ansible_module, self_created=False):
        self.tarfile = path
        self.module = ansible_module
        self.check_mode = self.module.check_mode
        self.self_created = self_created

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
            tar_listed_dirs = [" ".join(line.split()[5:]) for line in lines if line.split()[0][:1] == "d"]
            # tar doesn't list directories contain no file, so
            unlisted_dirs = [set(d.split("/")[:-1]) for d in tar_listed_dirs if "/" in d]
            unlisted_dirs = set.union(*unlisted_dirs)
            if unlisted_dirs:
                dirs = set.union(unlisted_dirs, set(tar_listed_dirs))
            else:
                unlisted_dirs = set([])
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
        files, dirs = self.list()
        if self.check_mode:
            return
        self._add_leading_slash(list(files2update))
        for f in files2update:
            command = ['tar', '-xf', self.tarfile, "-C", target, f]
            _ = self._runner(command)


def umask2mode(umask):
    perms = [i.split("=")[1] for i in umask.split(",")]
    perms = ["-" if not i else i for i in perms]
    to_num = lambda x: x.replace("r", "4").replace("w", "2").replace("x", "1").replace("-", "0")
    mode_num = lambda x: sum([int(i) for i in to_num(x)])
    mode_string = "".join([str(mode_num(i)) for i in perms])
    return mode_string


def check_permissions(dest, uid, gid, perms, dir_perms):
    dest_files, dest_dirs = get_files(dest)
    files = [File(os.path.abspath(f)) for f in dest_files]
    files = [f for f in files if f.path]
    dirs = [File(os.path.abspath(d)) for d in dest_dirs]
    dirs = [d for d in dirs if d.path]
    files_ownership2update = set([f for f in files if (f.uid != uid)] + [f for f in files if (f.gid != gid)])
    dirs_ownership2update = set([d for d in dirs if (d.uid != uid or d.gid != gid)])
    ownership2update = list(files_ownership2update) + list(dirs_ownership2update)
    files_mode2update = [f for f in files if f.mode != perms]
    dirs_mode2update = [d for d in dirs if d.mode != dir_perms]
    return ownership2update, files_mode2update, dirs_mode2update


def get_files(target):
    files = set()
    dirs = list()
    for path, sub_dirs, _files in os.walk(target):
        if os.path.isdir(path):
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
        except OSError as e:
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
        "_arch_root": dict(required=False),
        "verbose": dict(default=True, type='bool'),
        "remote_tmp": dict(default="/tmp"),
        "specialx": dict(default=False, type='bool'),
        "_tmpfile": dict(required=False),
        "source_is_directory": dict(required=True),
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
    verbose = params["verbose"]
    specialx = params["specialx"]
    source_is_directory = ["source_is_directory"]
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
    updated = None
    diff = {
        'before': dict(updated=dict()),
        'after': dict(updated=dict())
    }
    diff_updated_ownership_before = dict()
    diff_updated_mode_before = dict()

    tarfile = TarFile(path=tmpfile, ansible_module=module)

    exit_cmd = "module.exit_json(failed=failed, changed=changed, msg=dict(enumerate(msg)), diff=diff"
    if os.path.exists(dest):
        _update_msg = "differ(s)" if check_mode else "updated"
        if not os.path.isdir(dest):
            failed = True
            msg = "Destination (%s) is not a directory" % dest
            eval(exit_cmd)
        elif os.listdir(dest) != "":
            files2update = tarfile.compare(target=dest)
            if files2update:
                changed = True
                for f in files2update:
                    file_object = File(path=os.path.join(dest, f))
                    diff['before']['updated'][os.path.join(dest, f)] = file_object.size()
                tarfile.update(target=dest, files2update=files2update)
                updated = set([os.path.join(dest, d) for d in files2update])

                for f in updated:
                    file_object = File(path=f)
                    diff_updated_ownership_before[f] = "{}/{}".format(file_object.get_owner_name(),
                                                                      file_object.get_group_name())
                    diff_updated_mode_before[f] = file_object.mode
                    diff['after']['updated'][f] = file_object.size()
                msg.append("%s file(s) %s " % (len(files2update), _update_msg))

        if identical and os.listdir(dest) != "":
            spare_files, spare_dirs = make_identical(target=dest, tarfile=tarfile)
            if (spare_files or spare_dirs) and not check_mode:
                failed2remove = remove_spares(files=spare_files, dirs=spare_dirs)
                if failed2remove:
                    module.exit_json(failed=True, changed=False, msg=str(failed2remove))
            removed = spare_files | spare_dirs
            if removed:
                changed = True
                _remove_msg = "would be removed" if check_mode else "removed"
                msg.append("%s file(s) and %s dir(s) %s" % (len(spare_files), len(spare_dirs), _remove_msg))
                if removed:
                    if check_mode:
                        diff['before']['removed'] = removed
                    else:
                        diff['before']['removed'] = removed
                    diff['after']['would_be_removed'] = []

        dir_mode = perms_with_exec(mode) if specialx else mode
        ownership2update, files_mode2update, dirs_mode2update = check_permissions(dest=dest, uid=uid, gid=gid,
                                                                                  perms=mode, dir_perms=dir_mode)

        if ownership2update:
            changed = True
            ownership_before = dict()
            ownership_after = dict()
            for file_object in ownership2update:
                ownership_before[file_object.path] = "{}/{}".\
                    format(file_object.get_owner_name(), file_object.get_group_name())
                ownership_after[file_object.path] = "{}/{}".format(pwd.getpwuid(uid).pw_name, grp.getgrgid(gid).gr_name)
                if not check_mode:
                    file_object.set_owner(uid, gid)
            diff['before']['ownership'] = ownership_before
            diff['after']['ownership'] = ownership_after
            diff['before']['ownership'].update(diff_updated_ownership_before)

        diff['before']['mode'] = dict()
        diff['after']['mode'] = dict()
        mode_before = diff_updated_mode_before
        mode_after = dict()
        if files_mode2update:
            changed = True
            for file_object in files_mode2update:
                mode_before[file_object.path] = file_object.mode
                mode_after[file_object.path] = mode
                if not check_mode:
                    file_object.set_mode(mode)
        if dirs_mode2update:
            changed = True
            for directory in dirs_mode2update:
                mode_before[directory.path] = directory.mode
                mode_after[directory.path] = dir_mode
                if not check_mode:
                    directory.set_mode(dir_mode)
        if files_mode2update or dirs_mode2update:
            diff['before']['mode'].update(mode_before)
            diff['after']['mode'].update(mode_after)

        if not changed:
            msg = ["No update needed."]
        elif verbose:
            if diff['before']['mode']:
                msg.append("%s file/dir mode(s) %s" % (len(files_mode2update) + len(dirs_mode2update), _update_msg))
            if ownership2update:
                msg.append("%s file/dir ownership %s" % (len(ownership2update), _update_msg))
            if removed:
                if check_mode:
                    exit_cmd += ", would_be_removed=list(removed)"
                else:
                    exit_cmd += ", removed=list(removed)"
            if updated:
                if check_mode:
                    exit_cmd += ", files_to_update=list(updated)"
                else:
                    exit_cmd += ", updated_files=list(updated)"
        exit_cmd += ")"
        eval(exit_cmd)

    # Target doesn't exist -> untar
    if not check_mode:
        os.mkdir(dest)
        tarfile.untar(target=dest)
        check_permissions(dest=dest, uid=uid, gid=gid, perms=mode)
        changed = True
        _msg = "copied" if source_is_directory else "extracted"
        msg = "%s %s to %s " % (src, _msg, dest)
        updated = True
        if verbose:
            exit_cmd += ", 'extracted'=updated)"
    else:
        msg = "Target directory does not exists."
    failed = False

    eval(exit_cmd)

if __name__ == '__main__':
    main()

# Ansible module:  _fast_ recursive copy

It is custom Ansible module for copying directories recursively and fast.
&nbsp;  
The module intends to solve the old problem with the core copy module: you cannot use it with lots of files. As the Ansible documentation states about the copy module:
> The “copy” module recursively copy facility does not scale to lots (>hundreds) of files.

Using synchronize instead of it may be inconvenient in many cases.
#### Options:

parameter |	required | default | choices | comments
---|---|---|---|---
chdir | no | no | yes/no | If `yes`,  set executable flags to the directories for all users have any right to the directories (eg. if mode=640, it will be 750 for directories) Alias: x4dirs.
dest | yes | | |Remote absolute path where the file should be copied to. This must be a directory. If dest is a nonexistent path, dest is created. The parent directory of dest isn't created: the task fails if it doesn't already exist.
group |	no | | | Name or GID of the group that should own the file/directory, as would be fed to chown.
idenctical | no | no | yes/no | If `yes`, it will delete all files and dirs which are not in the source. (Makes an 'identical' copy.) Alias: delete
mode | no | | | Mode the file or directory should be. For those used to /usr/bin/chmod remember that modes are actually octal numbers (like 0644 or 740).
owner |	no | | | Name or the UID of the user that should own the file/directory, as would be fed to chown.
src | no | | | Local path to a directory to copy to the remote server; can be absolute or relative - it is copied recursively.
verbose | no | yes | yes/no | If `yes`, it provides detailed information about the differences between src and dest (running the module in verbose mode (-v))
###### Run-example:
dircopy_test.yml:
```yaml
---
- hosts: test.ho.st
  tasks:
  - name: Dircopy (custom module)
    dircopy: src=/opt/tmp/go/ dest=/opt/dc_test owner=jboss group=jboss mode=0640 delete=yes verbose=true
  - name: Ansible copy (core module)
    copy: src=/opt/tmp/go/  dest=/opt/copy_test owner=jboss group=jboss mode=0640

  become: yes
  become_user: root
  become_method: sudo
```
Out:
```bash
$  ansible-playbook -i inv dircopy_test.yml --user ansible --ask-pass --ask-sudo-pass -v
Using /etc/ansible/ansible.cfg as config file
SSH password:
SUDO password[defaults to SSH password]:

PLAY [test.ho.st] ***********************************************

TASK [setup] *******************************************************************
Tuesday 12 September 2017  11:53:34 +0200 (0:00:00.056)       0:00:00.056 *****
ok: [test.ho.st]

TASK [Ansible copy (core module)] **********************************************
Tuesday 12 September 2017  11:53:35 +0200 (0:00:00.940)       0:00:00.996 *****
changed: [test.ho.st] => {
    "changed": true,
    "dest": "/opt/copy_test/",
    "src": "/opt/tmp/go/misc"
}

TASK [Dircopy test (custom)] ***************************************************
Tuesday 12 September 2017  12:02:49 +0200 (0:09:14.353)       0:09:15.349 *****
changed: [test.ho.st] => {
    "changed": true,
    "msg": "/opt/tmp/go/misc copied to /opt/dc_test/ "
}

PLAY RECAP *********************************************************************
test.ho.st:  ok=3    changed=2    unreachable=0    failed=0

Tuesday 12 September 2017  12:02:50 +0200 (0:00:01.344)       0:09:16.694 *****
===============================================================================
Ansible copy (core module) -------------------------------------------- 554.35s
Dircopy test (custom module) -------------------------------------------- 1.34s
setup ------------------------------------------------------------------- 0.94s
```
(tasks' timing with [profile_tasks](https://github.com/jlafon/ansible-profile/blob/master/callback_plugins/profile_tasks.py))
##### Limitations:
- won't work on windows
- not tested with SElinux
- ...


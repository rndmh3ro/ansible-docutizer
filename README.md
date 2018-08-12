# ansible-docutizer

Create documentation from Ansible playbooks.

## Installing

Use `pip` to install the requirements.

```
pip install -r requirements.txt
```

## Running

`ansible-docutizer.py` can be run like the normal `ansible-playbook` command, but with some extra options.

For example, if our inventory file is called `development` and our playbook is `my_playbook.yml`, we can run `ansible-docutizer.py` with the following:

```
./ansible-docutizer.py --template-path templates/markdown_sample -o output.md -i development my_playbook.yml
```

This will parse the playbook, then template the data using `templates/markdown_sample/master.j2`.
The output will be saved to `output.md`.

> **NOTE:** If you want to keep your playbook/inventory files in a separate location, you may need to set `$ANSIBLE_ROLES_PATH` before running.
>
> ```
> ANSIBLE_ROLES_PATH=../foo/roles ./ansible-docutizer.py ... -i ../foo/development ../foo/my_playbook.yml
> ```

## Sample Template

The sample template is _very_ basic and is meant as a jumping off point for your own templates.

The sample template will generate a simple Markdown document, but the only Ansible module that is implemented is `lineinfile`.
Other modules will show a `not implemented` warning.

# Data Structure

**TODO:** Fill in data regarding data structure passed to templating engine.


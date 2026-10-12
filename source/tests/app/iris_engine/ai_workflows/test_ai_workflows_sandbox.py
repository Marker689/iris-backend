#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""The sandbox of the `python` node: what scripts may do, what they may
not reach, and the budgets that stop them."""

import io
import json
import os
import signal
import subprocess
import sys
from unittest import TestCase
from unittest import skipUnless
from unittest.mock import MagicMock
from unittest.mock import patch

from app.iris_engine.ai_workflows import sandbox
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_check
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_execute
from app.iris_engine.ai_workflows.sandbox import ai_workflows_sandbox_run_unconfined

_LINUX = sys.platform.startswith('linux')


def _run(source, variables=None, **kwargs):
    return ai_workflows_sandbox_run_unconfined(source, variables or {}, **kwargs)


def _result(source, variables=None):
    outcome = _run(source, variables)
    if not outcome['ok']:
        raise AssertionError(outcome)
    return outcome['result']


class TestsSandboxLanguage(TestCase):

    def test_result_variable_should_be_the_output(self):
        self.assertEqual({'a': 1}, _result("result = {'a': 1}"))

    def test_top_level_return_should_be_the_output(self):
        self.assertEqual(3, _result('return 1 + 2'))

    def test_no_result_should_give_none(self):
        self.assertIsNone(_result('x = 1'))

    def test_globals_should_be_the_given_variables(self):
        self.assertEqual('MD5', _result("result = inputs['type'].upper()", {'inputs': {'type': 'md5'}}))

    def test_control_flow_and_functions_should_work(self):
        source = '\n'.join((
            'def fact(n):',
            '    return 1 if n < 2 else n * fact(n - 1)',
            'total = 0',
            'for i in range(10):',
            '    if i % 2:',
            '        continue',
            '    total += i',
            'n = 0',
            'while True:',
            '    n += 1',
            '    if n > 3:',
            '        break',
            'result = [fact(5), total, n]',
        ))
        self.assertEqual([120, 20, 4], _result(source))

    def test_comprehensions_lambdas_and_unpacking_should_work(self):
        source = '\n'.join((
            "pairs = [(k, v) for k, v in {'a': 2, 'b': 1}.items() if v]",
            'first, second = sorted(pairs, key=lambda p: p[1])',
            "squares = {k: v * v for k, v in pairs}",
            "result = [first, second, squares, {x for x in [1, 1, 2]}]",
        ))
        self.assertEqual([['b', 1], ['a', 2], {'a': 4, 'b': 1}, [1, 2]], _result(source))

    def test_strings_and_fstrings_should_work(self):
        source = "x = 'A,b , c'\nresult = [p.strip().lower() for p in x.split(',')] + [f'{3.14159:.2f}', f'{7:03d}']"
        self.assertEqual(['a', 'b', 'c', '3.14', '007'], _result(source))

    def test_subscript_assignment_should_work(self):
        self.assertEqual({'k': [6, 2]}, _result("d = {'k': [1, 2]}\nd['k'][0] += 5\nresult = d"))

    def test_try_except_should_catch_script_errors(self):
        self.assertEqual("Missing key 'a'", _result("try:\n    x = {}['a']\nexcept Exception as e:\n    result = e"))
        self.assertTrue(_result("try:\n    int('x')\nexcept:\n    result = True"))

    def test_helpers_should_work(self):
        source = '\n'.join((
            "url_id = base64_encode('http://example.org/a', urlsafe=True, padding=False)",
            "data = json_parse('{\"a\": [1, 2]}')",
            "match = re_search('(\\\\d+)', 'abc 42')",
            "result = [url_id, data['a'], match['groups'][0], sha256('x')[:8], ip_info('10.0.0.1')['is_private'],",
            "          url_parse('https://e.org:8443/p?q=1')['host'], type_of(None), iso_datetime(1700000000),",
            "          iso_datetime('x')]",
        ))
        self.assertEqual(['aHR0cDovL2V4YW1wbGUub3JnL2E', [1, 2], '42', '2d711642', True, 'e.org', 'none',
                          '2023-11-14T22:13:20Z', None],
                         _result(source))

    def test_print_should_be_captured(self):
        outcome = _run("print('hello', 1)\nresult = 1")
        self.assertEqual(['hello 1'], outcome['logs'])

    def test_fail_should_end_the_script_and_not_be_catchable(self):
        outcome = _run("try:\n    fail('nope')\nexcept:\n    result = 'caught'")
        self.assertFalse(outcome['ok'])
        self.assertEqual('failed', outcome['kind'])
        self.assertEqual('nope', outcome['error'])
        self.assertEqual(2, outcome['line'])

    def test_errors_should_carry_the_line(self):
        outcome = _run("x = 1\ny = x / 0")
        self.assertFalse(outcome['ok'])
        self.assertEqual(2, outcome['line'])


class TestsSandboxContainment(TestCase):

    def _refused(self, source):
        self.assertTrue(ai_workflows_sandbox_check(source), source)
        self.assertFalse(_run(source)['ok'], source)

    def test_imports_should_be_refused(self):
        self._refused('import os')
        self._refused('from os import system')

    def test_attribute_access_should_be_refused(self):
        self._refused("x = ''.__class__")
        self._refused("x = (1).real")
        self._refused("x = [].append")

    def test_dunder_and_private_names_should_be_refused(self):
        self._refused("__import__('os')")
        self._refused("_x = 1")
        self._refused("def f(_a):\n    return _a")

    def test_unknown_or_private_methods_should_be_refused(self):
        self._refused("''.format(1)")
        self._refused("''.__add__('x')")
        self._refused("''.encode('utf-8')")

    def test_dangerous_statements_should_be_refused(self):
        for source in ('class A:\n    pass', 'global x', 'with x as y:\n    pass', "raise Exception('x')",
                       'async def f():\n    pass', 'del x', 'f(*args)', 'f(**kwargs)', 'x = yield 1',
                       '@d\ndef f():\n    pass', 'assert False', 'x := 1'):
            self.assertTrue(ai_workflows_sandbox_check(source), source)

    def test_python_builtins_should_not_exist(self):
        for name in ('open', 'eval', 'exec', 'compile', 'getattr', 'globals', 'vars', 'type', 'object',
                     'breakpoint', 'input', '__builtins__'):
            outcome = _run(f'result = {name}')
            self.assertFalse(outcome['ok'], name)

    def test_builtin_values_should_not_leak_python_objects(self):
        self.assertEqual('<builtin len>', _result('result = str(len)'))
        self.assertEqual('function', _result('def f():\n    pass\nresult = type_of(f)'))

    def test_percent_formatting_should_be_refused(self):
        self.assertFalse(_run("result = '%s' % 1")['ok'])

    def test_unknown_format_spec_should_be_refused(self):
        self.assertFalse(_run("result = f'{1:{\"9\" * 9}}'")['ok'])

    def test_output_should_be_plain_json(self):
        self.assertEqual({'t': [1, 2], 's': ['a'], 'f': None}, _result("result = {'t': (1, 2), 's': {'a'}, "
                                                                      "'f': float('inf')}"))
        self.assertEqual('<function f>', _result('def f():\n    pass\nresult = f'))

    def test_set_changed_while_iterated_should_not_raise(self):
        self.assertEqual([1, 2, 3], _result('s = {1, 2, 3}\nx = map(lambda v: s.add(v + 10), s)\nresult = '
                                            'sorted([v for v in s if v < 10])'))

    def test_complex_numbers_should_be_refused(self):
        outcome = _run('x = (-8) ** 0.5')
        self.assertEqual((False, 'error'), (outcome['ok'], outcome['kind']))

    def test_slices_outside_brackets_should_be_refused(self):
        # From Python 3.12 on a slice is hashable: `d[1:2, 3]` would make one a value
        self._refused('d = {}\nd[1:2, 3] = 1')
        self._refused('x = [1, 2][1:2, 0]')
        self.assertEqual([[2], [1, 3]], _result('x = [1, 2, 3]\nresult = [x[1:2], x[::2]]'))

    def test_keys_should_only_hold_plain_values(self):
        self.assertFalse(_run('d = {}\nd[(1, len)] = 1')['ok'])
        self.assertFalse(_run('x = {(1, (2, len))}')['ok'])

    def test_error_messages_should_be_bounded(self):
        outcome = _run("s = '<' * 100000\nx = f'{1:{s}}'")
        self.assertFalse(outcome['ok'])
        self.assertLess(len(outcome['error']), 600)


class TestsSandboxBudgets(TestCase):

    def _limit(self, source, **kwargs):
        outcome = _run(source, **kwargs)
        self.assertFalse(outcome['ok'], source)
        self.assertEqual('limit', outcome['kind'], outcome)
        return outcome

    def test_infinite_loop_should_hit_the_step_budget(self):
        self._limit('while True:\n    pass', max_steps=5000)

    def test_limit_errors_should_not_be_catchable(self):
        self._limit('try:\n    while True:\n        pass\nexcept:\n    result = 1', max_steps=5000)

    def test_deep_recursion_should_be_stopped(self):
        self._limit('def f(n):\n    return f(n + 1)\nf(0)')

    def test_big_values_should_be_refused(self):
        self._limit("x = 'a' * 10\nwhile True:\n    x = x + x")
        self._limit("x = 'a' * 100000000")
        self._limit('x = 2 ** 100000')
        self._limit('x = 1 << 100000')
        self._limit('x = list(range(10000000))')
        self._limit("x = 'ab'.replace('a', 'a' * 900000) * 2")
        self._limit("x = ' '.join(['a' * 1000] * 2000)")

    def test_self_referencing_values_should_be_stopped(self):
        self._limit('x = [1]\nx.append(x)\nresult = x')

    def test_time_budget_should_stop_the_script(self):
        self._limit('while True:\n    pass', max_steps=2_000_000, timeout_seconds=0.05)

    def test_oversized_source_should_be_refused(self):
        self.assertTrue(ai_workflows_sandbox_check('x = 1\n' * 5000))


@skipUnless(_LINUX, 'the confined child process needs Linux (seccomp)')
class TestsSandboxProcess(TestCase):

    def test_execute_should_run_in_a_child_process(self):
        outcome = ai_workflows_sandbox_execute("print('hi')\nresult = sum(inputs['n'])", {'inputs': {'n': [1, 2]}},
                                               timeout_seconds=5)
        self.assertEqual({'ok': True, 'result': 3, 'logs': ['hi']}, {k: outcome[k] for k in ('ok', 'result', 'logs')})

    def test_execute_should_refuse_invalid_scripts_without_starting(self):
        outcome = ai_workflows_sandbox_execute('import os', {})
        self.assertEqual('invalid', outcome['kind'])

    def test_catastrophic_regex_should_be_killed(self):
        outcome = ai_workflows_sandbox_execute("x = re_search('(a+)+$', 'a' * 40 + 'b')", {}, timeout_seconds=1)
        self.assertFalse(outcome['ok'])
        self.assertEqual('limit', outcome['kind'])

    def test_confined_child_should_still_run_every_builtin(self):
        outcome = ai_workflows_sandbox_execute(f"{sandbox._WARM_UP}\nresult = re_findall('[[b]', 'b')", {})
        self.assertEqual((True, ['b']), (outcome['ok'], outcome.get('result')), outcome)

    def test_crash_of_the_interpreter_should_only_stop_the_child(self):
        # Hashing recurses in C once per level. 99999 levels fitted the 8 MB
        # stack of the CI image (no crash) but not of an arm64 host; 900000
        # need over 8 MB on any build. 50 levels a statement: ~2 steps a
        # level, which keeps the build under MAX_STEPS.
        level = '(' * 50 + 't,' + '),' * 49 + ')'
        source = f't = ()\nfor i in range(18000):\n    t = {level}\nresult = len(set([t]))'
        outcome = ai_workflows_sandbox_execute(source, {}, max_steps=sandbox.MAX_STEPS, timeout_seconds=10)
        self.assertEqual((False, 'limit'), (outcome['ok'], outcome['kind']), outcome)
        self.assertIn('crashed', outcome['error'])


def _confined(statement):
    """Run `statement` as Python in a child confined like the sandbox one:
    what would happen to a script that got out of the interpreter."""
    code = '\n'.join((
        'import mmap, os, socket, sys',
        f'sys.path.insert(0, {os.path.dirname(sandbox.__file__)!r})',
        'import sandbox',
        'sandbox._confine_child(5)',
        statement,
        "sys.stdout.write('alive')",
        'sys.stdout.flush()',
        'os._exit(0)',
    ))
    return subprocess.run([sys.executable, '-I', '-S', '-c', code], capture_output=True, timeout=30, check=False)


@skipUnless(_LINUX, 'seccomp needs Linux')
class TestsSandboxConfinement(TestCase):

    def test_confined_child_should_compute_and_write_its_reply(self):
        completed = _confined("x = sorted(str(i) * 1000 for i in range(10000))")
        self.assertEqual((0, b'alive'), (completed.returncode, completed.stdout))

    def test_forbidden_system_calls_should_kill_the_child(self):
        for statement in ("open('/etc/hostname').read()", 'socket.socket()', 'os.fork()',
                          "os.execv('/bin/sh', ['sh'])", 'mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_EXEC)',
                          "os.write(3, b'x')", 'os.kill(os.getppid(), 0)', 'os.getpid()'):
            completed = _confined(statement)
            self.assertEqual(-signal.SIGSYS, completed.returncode, statement)
            self.assertEqual(b'', completed.stdout, statement)

    def test_killed_child_should_be_reported_as_forbidden(self):
        outcome = sandbox._stopped(-signal.SIGSYS, False)
        self.assertEqual(('error', 'The script was stopped: it attempted a forbidden operation'),
                         (outcome['kind'], outcome['error']))
        self.assertEqual('The script output is too large', sandbox._stopped(0, True)['error'])


def _verdict(instructions, arch, nr, arg0=0, arg2=0):
    """What the BPF program decides for a system call."""
    data = {sandbox._SECCOMP_DATA_ARCH: arch, sandbox._SECCOMP_DATA_NR: nr, sandbox._SECCOMP_DATA_ARG0: arg0,
            sandbox._SECCOMP_DATA_ARG2: arg2}
    pc, accumulator = 0, 0
    while True:
        code, jt, jf, k = instructions[pc]
        if code == sandbox._BPF_CODES['ret']:
            return k
        if code == sandbox._BPF_CODES['ld']:
            accumulator = data[k]
            pc += 1
            continue
        taken = {sandbox._BPF_CODES['jeq']: accumulator == k, sandbox._BPF_CODES['jge']: accumulator >= k,
                 sandbox._BPF_CODES['jset']: bool(accumulator & k)}[code]
        pc += 1 + (jt if taken else jf)


class TestsSandboxSeccompFilter(TestCase):

    _OTHERS = {'x86_64': {'openat': 257, 'socket': 41, 'execve': 59, 'clone': 56, 'ptrace': 101, 'read': 0},
               'aarch64': {'openat': 56, 'socket': 198, 'execve': 221, 'clone': 220, 'ptrace': 117, 'read': 63}}

    def test_filter_should_allow_only_the_listed_calls(self):
        allow, kill = sandbox._SECCOMP_RET_ALLOW, sandbox._SECCOMP_RET_KILL_PROCESS
        for machine, (arch, numbers) in sandbox._SYSCALLS.items():
            instructions = sandbox._seccomp_filter(machine)
            for name in sandbox._ALLOWED_SYSCALLS:
                self.assertEqual(allow, _verdict(instructions, arch, numbers[name]), (machine, name))
            for name, nr in self._OTHERS[machine].items():
                self.assertEqual(kill, _verdict(instructions, arch, nr), (machine, name))
            self.assertEqual(allow, _verdict(instructions, arch, numbers['write'], arg0=1))
            self.assertEqual(allow, _verdict(instructions, arch, numbers['write'], arg0=2))
            self.assertEqual(kill, _verdict(instructions, arch, numbers['write'], arg0=3))
            for name in ('mmap', 'mprotect'):
                self.assertEqual(allow, _verdict(instructions, arch, numbers[name], arg2=0x3), (machine, name))
                self.assertEqual(kill, _verdict(instructions, arch, numbers[name], arg2=0x5), (machine, name))
            # Another ABI (i386, arm32), or the x32 one sharing the x86_64 arch
            self.assertEqual(kill, _verdict(instructions, 0x40000003, numbers['write'], arg0=1))
            self.assertEqual(kill, _verdict(instructions, arch, sandbox._X32_SYSCALL_BIT | numbers['write'], arg0=1))

    def test_unknown_architecture_should_have_no_filter(self):
        with self.assertRaises(RuntimeError):
            sandbox._seccomp_filter('sparc64')


class TestsSandboxChildFailClosed(TestCase):

    def _child_main(self, confine):
        request = json.dumps({'source': "result = 'ran'", 'variables': {}}).encode('utf-8')
        stdout = io.StringIO()
        with patch.object(sandbox, '_confine_child', confine), \
                patch.object(sandbox.sys, 'stdin', io.TextIOWrapper(io.BytesIO(request))), \
                patch.object(sandbox.sys, 'stdout', stdout), \
                patch.object(sandbox.os, '_exit', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                sandbox._child_main()
        return json.loads(stdout.getvalue())

    def test_script_should_run_once_confined(self):
        self.assertEqual('ran', self._child_main(MagicMock())['result'])

    def test_script_should_not_run_when_the_confinement_fails(self):
        outcome = self._child_main(MagicMock(side_effect=OSError(1, 'prctl(PR_SET_SECCOMP) failed')))
        self.assertFalse(outcome['ok'])
        self.assertIn('could not be confined', outcome['error'])

    def test_root_should_become_nobody(self):
        nobody = sandbox._UNPRIVILEGED_ID
        fake_os = MagicMock(**{'getuid.return_value': 0, 'geteuid.return_value': 0,
                               'getresuid.return_value': (nobody,) * 3, 'getresgid.return_value': (nobody,) * 3,
                               'getgroups.return_value': []})
        with patch.object(sandbox, 'os', fake_os):
            sandbox._drop_privileges()
        fake_os.setgroups.assert_called_once_with([])
        fake_os.setresgid.assert_called_once_with(nobody, nobody, nobody)
        fake_os.setresuid.assert_called_once_with(nobody, nobody, nobody)

    def test_root_kept_should_fail(self):
        fake_os = MagicMock(**{'getuid.return_value': 0, 'geteuid.return_value': 0,
                               'getresuid.return_value': (0, 0, 0), 'getresgid.return_value': (0, 0, 0),
                               'getgroups.return_value': []})
        with patch.object(sandbox, 'os', fake_os), self.assertRaises(RuntimeError):
            sandbox._drop_privileges()

    def test_unprivileged_user_should_be_kept(self):
        fake_os = MagicMock(**{'getuid.return_value': 1000, 'geteuid.return_value': 1000})
        with patch.object(sandbox, 'os', fake_os):
            sandbox._drop_privileges()
        fake_os.setresuid.assert_not_called()

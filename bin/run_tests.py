#!/usr/bin/env python2
# vim: ts=4 et sw=4 sts=4 :

# security scanner - scan a system's security related information
# Copyright (C) 2017 SUSE LINUX GmbH
#
# Author: Benjamin Deuter, Sebastian Kaim
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# version 2 as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301 USA.

# Standard library modules
from __future__ import print_function
from __future__ import with_statement
import threading
import argparse
import os
import subprocess
import tempfile
import shutil


SSCANNER_PATH = os.path.join(os.path.realpath(os.path.dirname(__file__)), 'security_scanner.py')


class SscannerTest(object):

    PARAM_VARIATIONS = [
        [],  # default - show processes
        ['--params'],  # show processes w/ parameters
        ['--fd'],  # show processes w/ file descriptors
        ['--onlyfd'],  # only show file descriptors
        ['--filesystem', '-s']  # show interesting files on filesystem
    ]

    def __init__(self):
        self._setupArgparse()
        self.m_testcases = []
        self.m_params = None
        self.m_has_failed_tests = False
        self.m_running = 0
        self.m_testcount = 0

    def hasFailed(self):
        return self.m_has_failed_tests

    def _setupArgparse(self):
        description = "Testing tool for the security scanner. Runs the scanner in several configurations and returns " \
                      "the exit code as well as the output."
        parser = argparse.ArgumentParser(description=description)

        description = "The directory to store the testdata in. Defaults to a random directory in /tmp."
        parser.add_argument("-d", "--directory", type=str, help=description,
                            default=tempfile.mkdtemp(prefix='sscanner-test'))

        description = "Whether to run local tests (requires root access)"
        parser.add_argument("-l", "--local", action='store_true', help=description)

        description = "The remote to use (root@some-vm). If empty, remote test will be skipped."
        parser.add_argument("-r", "--remote", type=str, help=description, default='')

        # TODO
        # description = "The amount of threads to use. Defaults to 8. Use 1 to disable threading."
        # parser.add_argument("-j", "-t", "--threads", type=int, help=description, default=8)

        self.m_parser = parser

    def run(self, args=None):
        self.m_params = self.m_parser.parse_args(args=args)
        self.checkParams()
        self.prepareTests()
        self.runTests()
        self.printResults()
        self.cleanUp()

    def checkParams(self):

        if not self.m_params.local and not self.m_params.remote:
            print("Nothing to do (pass --local and/or --remote)")
            exit(1)

    def prepareTests(self):
        local = self.m_params.local
        remote = self.m_params.remote
        tdir = self.m_params.directory
        counter = 10

        if not os.path.exists(tdir):
            os.mkdir(tdir, 0o755)

        for variation in SscannerTest.PARAM_VARIATIONS:
            if local:
                self.m_testcases.append(TestCase(os.path.join(tdir, str(counter)), None, variation))
                counter += 1
            if remote:
                self.m_testcases.append(TestCase(os.path.join(tdir, str(counter)), remote, variation))
                counter += 1

    def runTests(self):
        self.m_testcount = len(self.m_testcases)
        self.m_running = self.m_testcount

        print("Running {} tests in {}.".format(self.m_testcount, self.m_params.directory))

        os.chmod(self.m_params.directory, 0o755)

        threads = []
        for tc in self.m_testcases:
            t = threading.Thread(target=self.runTest, args=[self, tc])
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        failed_tests = [item for testcase in self.m_testcases for item in testcase.getRuns() if item.hasFailed()]
        self.m_has_failed_tests = len(failed_tests) > 0

    def cleanUp(self):
        """Delete temporary files generated by the scanner."""
        print("Cleaning up ... ", end='')
        for test in self.m_testcases:
            test.clean()
        print("done")

    @staticmethod
    def runTest(inst, case):
        case.run()
        inst.m_running -= 1
        print("Test {}/{} finished.".format(inst.m_testcount - inst.m_running, inst.m_testcount))

    def printResults(self):
        tests = [item for testcase in self.m_testcases for item in testcase.getRuns()]
        amount = len(tests)
        failed_tests = [test for test in tests if test.hasFailed()]
        amount_failed = len(failed_tests)

        print("""
 Results
=========
Ran: {}
Successful: {}
Failed: {}
""".format(amount, amount-amount_failed, amount_failed))

        if amount_failed > 0:
            print("\nReports of failed tests:\n")
            print("\n--\n".join([str(test) for test in failed_tests]))


class TestCase(object):
    """This class represents a Testcase with a cached, an uncached and a --nocache run."""

    def __init__(self, directory, remote, arguments):
        self.dir = directory
        self.remote = remote
        self.arguments = arguments
        self.tests = [
            TestRun(os.path.join(self.dir, 'cached'), self._getModeArguments() + self.arguments, False, 'uncached'),
            TestRun(os.path.join(self.dir, 'cached'), self._getModeArguments() + self.arguments, True, 'cached'),
            TestRun(os.path.join(self.dir, 'nocache'), self._getModeArguments() + self.arguments + ['--nocache'], False,
                    'nocache')
        ]

    def _getModeArguments(self):
        """Gets the mode arguments for this test."""
        if not self.remote:
            return ['--mode', 'local']
        else:
            return ['--mode', 'ssh', '--entry', self.remote]

    def run(self):
        if not os.path.exists(self.dir):
            os.mkdir(self.dir, 0o755)  # not totally safe but safe enough

        for test in self.tests:
            test.run()

    def getRuns(self):
        return self.tests

    def clean(self):
        """Cleans up all temporary files, but leaves logs untouched."""
        for test in self.tests:
            test.clean()


class TestRun(object):
    """This class represents a run of the scanner."""
    def __init__(self, dir, arguments, cached, name):
        self.ran = False
        self.exitcode = 0
        self.dir = dir
        self.cached = cached
        self.name = name
        self.stdout = os.path.join(dir, "stdout-{}.txt".format(name))
        self.stderr = os.path.join(dir, "stderr-{}.txt".format(name))
        self.cachedir = os.path.join(dir, "sscanner-cache")
        self.m_arguments = ['-d', self.cachedir] + arguments

    def run(self):
        if not os.path.exists(self.dir):
            os.mkdir(self.dir, 0o755)

        with open(self.stdout, "w", 0o664) as stdout:
            with open(self.stderr, "w", 0o664) as stderr:
                self.exitcode = subprocess.call(
                    executable=SSCANNER_PATH,
                    args=[SSCANNER_PATH] + self.m_arguments,
                    stderr=stderr,
                    stdout=stdout
                )
                self.ran = True

    def clean(self):
        """Cleans up the scanner cache directories."""
        if os.path.isdir(self.cachedir):
            shutil.rmtree(self.cachedir)

    def __str__(self):
        status = (str(self.hasFailed()) + '({})'.format(self.exitcode)) if self.ran else "(not yet run)"

        return """ Testrun {} [Failed: {}] [Cached: {}]
------------------------------
dir: {}
command line: {}
stdout: {}
stderr: {} 
""".format(self.name, status, self.cached, self.dir, str(self.m_arguments), self.stdout, self.stderr)

    def hasFailed(self):
        return self.exitcode != 0


if __name__ == "__main__":
    tester = SscannerTest()
    tester.run()

    if tester.hasFailed():
        exit(2)

#!/usr/bin/env python

__authors__ = "Olivier Delalleau, Eric Larsen"
__contact__ = "delallea@iro"

"""
Run this script to run tests in small batches rather than all at the same time
or to conduct time-profiling.

If no argument is provided, then the whole Theano test-suite is run.
Otherwise, only tests found in the directory given as argument are run.

If 'time_profile=False', this script performs three tasks:
    1. Run `nosetests --collect-only --with-id` to collect test IDs
    2. Run `nosetests --with-id i1 ... iN` with batches of 'batch_size'
       indices, until all tests have been run (currently batch_size=100 by
       default).
    3. Run `nosetests --failed` to re-run only tests that failed
       => The output of this 3rd step is the one you should care about

If 'time_profile=True', this script conducts time-profiling of the tests:
    1. Run `nosetests --collect-only --with-id` to collect test IDs
    2. Run `nosetests --with-id i`, one test with ID 'i' at a time, collecting
       timing information and displaying progresses on standard output after
       every group of 'batch_size' (100 by default), until all tests have
       been run.
       The results are deposited in the files 'timeprof_sort' and
       'timeprof_nosort' in the current directory. Both contain one record for
       each test and comprise the following fields:
       - test running-time
       - nosetests sequential test number
       - test name
       - name of class to which test belongs (if any), otherwise full
         information is contained in test name
       - test outcome ('OK', 'SKIPPED TESTS', 'FAILED TEST' or 'FAILED PARSING')
       In 'timeprof_sort', test records are sorted according to run-time
       whereas in 'timeprof_nosort' records are reported according to
       sequential number. The former classification is the main information
       source for time-profiling. Since tests belonging to same or close
       classes and files have close sequential numbers, the latter may be used
       to identify duration patterns among the tests. A full log is also saved
       as 'timeprof_rawlog'.

One reason to use this script is if you are a Windows user, and see errors like
"Not enough storage is available to process this command" when trying to simply
run `nosetests` in your Theano installation directory. This error is apparently
caused by memory fragmentation: at some point Windows runs out of contiguous
memory to load the C modules compiled by Theano in the test-suite.

By using this script, nosetests is run on a small subset (batch) of tests until
all tests are run. Note that this is slower, in particular because of the
initial cost of importing theano and loading the C module cache on each call of
nosetests.
"""


import cPickle
import os
import subprocess
import sys
import datetime
import theano


def main(stdout=None, stderr=None, argv=None, theano_nose=None,
         batch_size=None, time_profile=False):
    """
    Run tests with optional output redirection.

    Parameters stdout and stderr should be file-like objects used to redirect
    the output. None uses default sys.stdout and sys.stderr.

    If argv is None, then we use arguments from sys.argv, otherwise we use the
    provided arguments instead.

    If theano_nose is None, then we use the theano-nose script found in
    Theano/bin to call nosetests. Otherwise we call the provided script.

    If batch_size is None, we use a default value of 100.
    """

    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    if argv is None:
        argv = sys.argv
    if theano_nose is None:
        theano_nose = os.path.join(theano.__path__[0], '..',
                                   'bin', 'theano-nose')
    if batch_size is None:
        batch_size = 100
    stdout_backup = sys.stdout
    stderr_backup = sys.stderr
    try:
        sys.stdout = stdout
        sys.stderr = stderr
        run(stdout, stderr, argv, theano_nose, batch_size, time_profile)
    finally:
        sys.stdout = stdout_backup
        sys.stderr = stderr_backup


def run(stdout, stderr, argv, theano_nose, batch_size, time_profile):

    # Setting aside current working directory for later saving
    sav_dir = os.getcwd()
    if len(argv) == 1:
        tests_dir = theano.__path__[0]
        other_args = []
    else:
        # tests_dir should be at the end of argv, there can be other arguments
        tests_dir = argv[-1]
        other_args = argv[1:-1]
        assert os.path.isdir(tests_dir)
    os.chdir(tests_dir)
    # It seems safer to fully regenerate the list of tests on each call.
    if os.path.isfile('.noseids'):
        os.remove('.noseids')

    # Collect test IDs.
    print """\
####################
# COLLECTING TESTS #
####################"""
    stdout.flush()
    stderr.flush()
    dummy_in = open(os.devnull)
    # We need to call 'python' on Windows, because theano-nose is not a
    # native Windows app; and it does not hurt to call it on Unix.
    # Using sys.executable, so that the same Python version is used.
    python = sys.executable
    rval = subprocess.call(
        ([python, theano_nose, '--collect-only', '--with-id']
         + other_args),
        stdin=dummy_in.fileno(),
        stdout=stdout.fileno(),
        stderr=stderr.fileno())
    stdout.flush()
    stderr.flush()
    assert rval == 0
    noseids_file = '.noseids'
    data = cPickle.load(open(noseids_file, 'rb'))
    ids = data['ids']
    n_tests = len(ids)
    assert n_tests == max(ids)

    # Standard batch testing is called for
    if not time_profile:
        failed = set()
        print """\
###################################
# RUNNING TESTS IN BATCHES OF %s #
###################################""" % batch_size
        # We suppress all output because we want the user to focus only on
        # the failed tests, which are re-run (with output) below.
        dummy_out = open(os.devnull, 'w')
        for test_id in xrange(1, n_tests + 1, batch_size):
            stdout.flush()
            stderr.flush()
            test_range = range(test_id, min(test_id + batch_size, n_tests + 1))
            rval = subprocess.call(
                ([python, theano_nose, '-q', '--with-id']
                 + map(str, test_range)
                 + other_args),
                stdout=dummy_out.fileno(),
                stderr=dummy_out.fileno(),
                stdin=dummy_in.fileno())
            # Recover failed test indices from the 'failed' field of the
            # '.noseids' file. We need to do it after each batch because
            # otherwise this field may get erased. We use a set because it
            # seems like it is not systematically erased though, and we want
            # to avoid duplicates.
            failed = failed.union(cPickle.load(open(noseids_file, 'rb'))
                                  ['failed'])
            print '%s%% done (failed: %s)' % ((test_range[-1] * 100) //
                                n_tests, len(failed))
        # Sort for cosmetic purpose only.
        failed = sorted(failed)
        if failed:
            # Re-run only failed tests
            print """\
################################
# RE-RUNNING FAILED TESTS ONLY #
################################"""
            stdout.flush()
            stderr.flush()
            subprocess.call(
                ([python, theano_nose, '-v', '--with-id']
                 + failed
                 + other_args),
                stdin=dummy_in.fileno(),
                stdout=stdout.fileno(),
                stderr=stderr.fileno())
            stdout.flush()
            stderr.flush()
            return 0
        else:
            print """\
####################
# ALL TESTS PASSED #
####################"""

    # Time-profiling is called for
    else:
        print """\
########################################
# RUNNING TESTS IN TIME-PROFILING MODE #
########################################"""

        # finds first word of list l containing string s
        def getIndexOfFirst(l, s):
            for pos, word in enumerate(l):
                if s in word:
                    return pos

        # finds last word of list l containing string s
        def getIndexOfLast(l, s):
            for pos, word in enumerate(reversed(l)):
                if s in word:
                    return (len(l) - pos - 1)

        # iterating through tests
        # initializing master profiling list and raw log
        prof_master_nosort = []
        prof_rawlog = []
        dummy_out = open(os.devnull, 'w')
        for test_floor in xrange(1, n_tests + 1, batch_size):
            for test_id in xrange(test_floor, min(test_floor + batch_size,
                                                 n_tests + 1)):
                proc = subprocess.Popen(
                    ([python, theano_nose, '-v', '--with-id']
                    + [str(test_id)] + other_args +
                     ['--disabdocstring']),
                    # the previous option calls a custom Nosetests plugin
                    # precluding automatic sustitution of doc. string for
                    # test name in display
                    # (see class 'DisabDocString' in file theano-nose)
                    stderr=subprocess.PIPE,
                    stdout=dummy_out.fileno(),
                    stdin=dummy_in.fileno())

                # recovering and processing data from pipe
                err = proc.stderr.read()
                # building the raw log
                prof_rawlog.append(err)
                # parsing the output
                l_err = err.split()
                try:
                    pos_id = getIndexOfFirst(l_err, '#')
                    prof_id = l_err[pos_id]
                    pos_dot = getIndexOfFirst(l_err, '...')
                    prof_test = ''
                    for s in l_err[pos_id + 1: pos_dot]:
                        prof_test += s + ' '
                    if 'OK' in err:
                        pos_ok = getIndexOfLast(l_err, 'OK')
                        if len(l_err) == pos_ok + 1:
                            prof_time = float(l_err[pos_ok - 1][0:-1])
                            prof_pass = 'OK'
                        elif 'SKIP' in l_err[pos_ok + 1]:
                            prof_time = 0.
                            prof_pass = 'SKIPPED TEST'
                        elif 'KNOWNFAIL' in l_err[pos_ok + 1]:
                            prof_time = float(l_err[pos_ok - 1][0:-1])
                            prof_pass = 'OK'
                        else:
                            prof_time = 0.
                            prof_pass = 'FAILED TEST'
                    else:
                        prof_time = 0.
                        prof_pass = 'FAILED TEST'
                except Exception:
                    prof_time = 0
                    prof_id = '#' + str(test_id)
                    prof_test = ('FAILED PARSING, see raw log for details'
                                 ' on test')
                    prof_pass = ''
                prof_tuple = (prof_time, prof_id, prof_test, prof_pass)
                # appending tuple to master list
                prof_master_nosort.append(prof_tuple)
            print '%s%% time-profiled' % ((test_id * 100) // n_tests)

        # sorting tests according to running-time
        prof_master_sort = sorted(prof_master_nosort,
                                  key=lambda test: test[0], reverse=True)

        # saving results to readable files
        path_nosort = os.path.join(sav_dir, 'timeprof_nosort')
        path_sort = os.path.join(sav_dir, 'timeprof_sort')
        path_rawlog = os.path.join(sav_dir, 'timeprof_rawlog')
        f_nosort = open(path_nosort, 'w')
        f_sort = open(path_sort, 'w')
        f_rawlog = open(path_rawlog, 'w')
        stamp = str(datetime.datetime.now()) + '\n\n'
        fields = ('Fields: computation time; nosetests sequential id;'
                  ' test name; parent class (if any); outcome\n\n')
        f_nosort.write('TIME-PROFILING OF THEANO\'S NOSETESTS'
                       ' (by sequential id)\n\n' + stamp + fields)
        f_sort.write('TIME-PROFILING OF THEANO\'S NOSETESTS'
                     ' (sorted by computation time)\n\n' + stamp + fields)
        for i in xrange(len(prof_master_nosort)):
            s_nosort = ((str(prof_master_nosort[i][0]) + 's').ljust(10) +
                 " " + prof_master_nosort[i][1].ljust(7) + " " +
                 prof_master_nosort[i][2] + prof_master_nosort[i][3] +
                 "\n")
            f_nosort.write(s_nosort)
            s_sort = ((str(prof_master_sort[i][0]) + 's').ljust(10) +
                 " " + prof_master_sort[i][1].ljust(7) + " " +
                 prof_master_sort[i][2] + prof_master_sort[i][3] +
                 "\n")
            f_sort.write(s_sort)
        f_nosort.close()
        f_sort.close()
        f_rawlog.write('TIME-PROFILING OF THEANO\'S NOSETESTS'
                ' (raw log)\n\n' + stamp)
        for i in xrange(len(prof_rawlog)):
            f_rawlog.write(prof_rawlog[i])
        f_rawlog.close()

if __name__ == '__main__':
    sys.exit(main())

    

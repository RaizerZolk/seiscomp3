#!/usr/bin/env python

###############################################################################
# Copyright (C) 2013-2014 by gempa GmbH
#
# Author:  Stephan Herrnkind
# Email:   herrnkind@gempa.de
###############################################################################

from __future__ import print_function

import os
import requests
import signal
import socket
import subprocess
import sys
import time
import traceback

from datetime import datetime, timedelta


###############################################################################
class FDSNWSTest:

    #--------------------------------------------------------------------------
    def __init__(self, port=8080):
        self.port = port
        self.url = 'http://localhost:{}/fdsnws'.format(self.port)
        self.service = None
        self.rootdir = os.environ.get('SEISCOMP_ROOT')
        self.sharedir = '{}/share/fdsnws'.format(self.rootdir)


    #--------------------------------------------------------------------------
    def __call__(self):
        if not self._startService():
            return 1

        try:
            self.test()
        except Exception as e:
            traceback.print_exc()

            self._stopService()
            return 1

        self._stopService()
        return 0


    #--------------------------------------------------------------------------
    def _waitForSocket(self, timeout=10):
        print('waiting for port {} to become ready '.format(self.port),
              end='')
        maxTime = datetime.now() + timedelta(timeout)
        while self.service is not None and self.service.poll() == None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            res = sock.connect_ex(('127.0.0.1', self.port))
            sock.close()
            if res == 0:
                print(' OK')
                return True

            if datetime.now() > maxTime:
                print(' TIMEOUT EXCEEDED')
                return False
            time.sleep(0.2)
            print('.', end='')

        print(' SERVICE TERMINATED')


    #--------------------------------------------------------------------------
    def _startService(self):
        cmd = self.command()
        print('starting FDSNWS service:', ' '.join(cmd))
        try:
            self.fdOut = open('fdsnws.stdout', 'w')
            self.fdErr = open('fdsnws.stderr', 'w')
            self.service = subprocess.Popen(cmd, stdout=self.fdOut,
                                            stderr=self.fdErr)
        except Exception, e:
            print('failed to start FDSNWS service:', str(e))
            return False

        if not self._waitForSocket():
            self._stopService()
            return False

        return True


    #--------------------------------------------------------------------------
    def _stopService(self, timeout=10):
        if self.service.poll() is not None:
            print('warning: FDSNWS service terminated ahead of time',
                  file=sys.stdout)
            return
        print('stopping FDSNWS service (PID: {}): '.format(self.service.pid),
              end='')
        maxTime = datetime.now() + timedelta(timeout)

        self.service.terminate()
        while self.service.poll() == None:
            print('.', end='')
            time.sleep(0.2)
            if datetime.now() > maxTime:
                print(' TIMEOUT EXCEEDED, sending kill signal',
                      file=sys.stdout)
                self.service.kill()
                return

        print(' OK')


    #--------------------------------------------------------------------------
    def test(self):
        pass


    #--------------------------------------------------------------------------
    def command(self):
        return [
            'python', '{}/../../fdsnws.py'.format(self.rootdir),
            '--verbosity=4',
            '--plugins=dbsqlite3,fdsnxml',
            '--database=sqlite3://{}/seiscomp3.sqlite3'.format(self.rootdir),
            '--serveAvailability=true', '--dataAvailability.enable=true',
            '--agencyID=Test',
            '--record-url=sdsarchive://{}/sds'.format(self.rootdir),
            '--htpasswd={}/fdsnws.htpasswd'.format(self.rootdir)
        ]



    #--------------------------------------------------------------------------
    def diff(self, expected, got, ignoreRanges):
        if expected == got:
            return (None, None)
        lenExp = minLen = maxLen = len(expected)
        lenGot = len(got)
        for r in ignoreRanges:
            if len(r) > 2:
                minLen -= r[2]
            if len(r) > 3:
                maxLen += r[3]

        if lenGot == 0 and minLen <= 0:
            return (None, None)
        if lenGot < minLen or lenGot > maxLen:
            return (min(lenExp,lenGot), 'read {} bytes, expected {}'.format(
                        lenGot, minLen if minLen == maxLen \
                                else '{}-{}'.format(minLen, maxLen)))

        # offset between got and expected index may result from variable length
        # result data, e.g. microseconds of time stamps
        iGot = iExp = 0
        while iExp < lenExp:
            if iGot >= lenGot:
                iGot = lenGot + 1
                break

            if got[iGot] == expected[iExp]:
                iExp += 1
                iGot += 1
                continue

            # bytes do not match, check ignore Range
            ignoreRange = None
            for r in ignoreRanges:
                if iExp >= r[0] and iExp < r[1]:
                    ignoreRange = r
                    break

            if ignoreRange:
                rStart = ignoreRange[0]
                rEnd = ignoreRange[1]
                rLeft = rEnd - iExp
                rFewer = ignoreRange[2] if len(ignoreRange) > 2 else 0
                rMore = ignoreRange[3] if len(ignoreRange) > 3 else 0
                varLen = rFewer + rMore

                # advance expected pointer behind range
                iExp = rEnd
                exp = expected[iExp] if iExp < lenExp else None

                # static range length: advance got pointer behind range
                if varLen == 0:
                    iGot += rLeft
                    continue

                # dynamic ignore range length: search end of range indicated
                # by current exp pointer but limited by rLenDiff
                iGot += min(rLeft, rLeft - rFewer)

                # expected data ends on ignore range
                if exp == None:
                    iGot += min(lenGot-iGot, varLen)
                    continue

                # search range end in data
                else:
                    pos = got[iGot:iGot+varLen+1].find(exp)
                    print('{}: iGot: {}, varLen: {}, exp: {}, pos: {}'
                          .format(got[iGot:iGot+rLeft], iGot, varLen, exp,
                                  pos))
                    if pos >= 0:
                        iGot += pos
                        continue

            return (iGot, '... [ {} ] != [ {} ] ...'.format(
                    got[max(0, iGot-10):min(lenGot, iGot+11)],
                    expected[max(0, iExp-10):min(lenExp, iExp+11)]))

        if iGot < lenGot:
            return (lenGot, 'read {} more bytes than expected'.format(
                            lenGot-iGot))
        elif iGot > lenGot:
            return (lenGot, 'read {} fewer bytes than expected'.format(
                            iGot-lenGot))

        # should not happen
        return (None, None)


    #--------------------------------------------------------------------------
    def testGET(self, url, contentType='text/html', data=None,
                dataFile=None, retCode=200, testID=None, ignoreRanges=[],
                auth=None):
        if testID is not None:
            print('#{} '.format(testID), end='')
        print('{}: '.format(url), end='')
        stream = False if dataFile is None else True
        r = requests.get(url, stream=stream, auth=auth)
        if r.status_code != retCode:
            raise ValueError('Invalid status code, expected "{}", got "{}"' \
                             .format(retCode, r.status_code))

        if contentType != r.headers['content-type']:
            raise ValueError('Invalid content type, expected "{}", got "{}"' \
                             .format(contentType, r.headers['content-type']))

        expected = None
        if data is not None:
            expected = data
        elif dataFile is not None:
            with open(dataFile, 'rb') as f:
                expected = f.read()

        if expected is not None:
            errPos, errMsg = self.diff(expected, r.content, ignoreRanges)
            if errPos is not None:
                raise ValueError('Unexpected content at byte {}: {}'.format(
                                 errPos, errMsg))

        print('OK')
        sys.stdout.flush()


# vim: ts=4 et tw=79

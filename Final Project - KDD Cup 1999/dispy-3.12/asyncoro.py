"""
asyncoro: Framework for concurrent, distributed, network programming
with asynchronous completions and coroutines.
See http://asyncoro.sourceforge.net for details.
"""

__author__ = "Giridhar Pemmasani (pgiri@yahoo.com)"
__email__ = "pgiri@yahoo.com"
__copyright__ = "Copyright 2012, Giridhar Pemmasani"
__contributors__ = []
__maintainer__ = "Giridhar Pemmasani (pgiri@yahoo.com)"
__license__ = "MIT"
__url__ = "http://asyncoro.sourceforge.net"
__status__ = "Production"
__version__ = "2.0"

__all__ = ['AsynCoroSocket', 'AsyncSocket', 'Coro', 'AsynCoro',
           'Lock', 'RLock', 'Event', 'Condition', 'Semaphore',
           'HotSwapException', 'MonitorException', 'Location', 'Channel',
           'AsynCoroThreadPool', 'AsynCoroDBCursor', 'MetaSingleton']

import time
import threading
import functools
import socket
import inspect
import traceback
import select
import sys
import types
import struct
import logging
import errno
import os
import stat
import hashlib
import platform
import random
import ssl
from heapq import heappush, heappop
from bisect import bisect_left
import Queue
import atexit
import collections
import cPickle as pickle
import copy

logger = logging.getLogger('asyncoro')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(message)s'))
logger.addHandler(handler)
del handler

if platform.system() == 'Windows':
    from errno import WSAEINPROGRESS as EINPROGRESS
    from errno import WSAEWOULDBLOCK as EWOULDBLOCK
    from errno import WSAEINVAL as EINVAL
    from time import clock as _time
    _time()
else:
    from errno import EINPROGRESS
    from errno import EWOULDBLOCK
    from errno import EINVAL
    from time import time as _time

def serialize(obj):
    return pickle.dumps(obj, pickle.HIGHEST_PROTOCOL)

def unserialize(pkl):
    return pickle.loads(pkl)

class MetaSingleton(type):
    __instance = None
    def __call__(cls, *args, **kwargs):
        if cls.__instance is None:
            cls.__instance = super(MetaSingleton, cls).__call__(*args, **kwargs)
        return cls.__instance

class _AsynCoroSocket(object):
    """Base class for use with AsynCoro, for asynchronous I/O
    completion and coroutines. This class is for internal use
    only. Use AsynCoroSocket, defined below, instead.
    """

    __slots__ = ('_rsock', '_keyfile', '_certfile', '_ssl_version', '_fileno', '_timeout',
                 '_timeout_id', '_read_coro', '_read_task', '_read_result', '_write_coro',
                 '_write_task', '_write_result', '_asyncoro', '_notifier', 'recvall', 'sendall',
                 'recv_msg', 'send_msg', '_blocking', 'recv', 'send', 'recvfrom', 'sendto',
                 'accept', 'connect')

    _default_timeout = None

    def __init__(self, sock, blocking=False, keyfile=None, certfile=None,
                 ssl_version=ssl.PROTOCOL_SSLv23):
        """Setup socket for use wih asyncoro.

        @blocking=True implies synchronous sockets and blocking=False
        implies asynchronous sockets.

        @keyfile, @certfile and @ssl_version are as per ssl's wrap_socket
        method.

        Only methods without leading underscore should be used; other
        attributes are for internal use only. In addition to usual
        socket I/O methods, AsynCoroSocket implemnents 'recvall',
        'send_msg', 'recv_msg' and 'unwrap' methods.
        """

        if isinstance(sock, AsynCoroSocket):
            logger.warning('Socket %s is already AsynCoroSocket', sock._fileno)
            for k in sock.__slots__:
                setattr(self, k, getattr(sock, k))
        else:
            self._rsock = sock
            self._keyfile = keyfile
            self._certfile = certfile
            self._ssl_version = ssl_version
            self._fileno = sock.fileno()
            self._timeout = 0
            self._timeout_id = None
            self._read_coro = None
            self._read_task = None
            self._read_result = None
            self._write_coro = None
            self._write_task = None
            self._write_result = None
            self._asyncoro = None
            self._notifier = None

            self.recvall = None
            self.sendall = None
            self.recv_msg = None
            self.send_msg = None

            self._blocking = None
            self.setblocking(blocking)
            # technically, we should set socket to blocking if
            # _default_timeout is None, but ignore this case
            if _AsynCoroSocket._default_timeout:
                self.settimeout(_AsynCoroSocket._default_timeout)

    def __getattr__(self, name):
        return getattr(self._rsock, name)

    def setblocking(self, blocking):
        if blocking:
            blocking = True
        else:
            blocking = False
        if self._blocking == blocking:
            return
        self._blocking = blocking
        if self._blocking:
            self._unregister()
            self._rsock.setblocking(1)
            if self._certfile:
                self._rsock = ssl.wrap_socket(self._rsock, keyfile=self._keyfile,
                                              certfile=self._certfile,
                                              ssl_version=self._ssl_version)
            for name in ['recv', 'send', 'recvfrom', 'sendto', 'accept', 'connect']:
                setattr(self, name, getattr(self._rsock, name))
            if self._rsock.type & socket.SOCK_STREAM:
                self.recvall = self._sync_recvall
                self.sendall = self._sync_sendall
                self.recv_msg = self._sync_recv_msg
                self.send_msg = self._sync_send_msg
            self._asyncoro = None
            self._notifier = None
        else:
            self._rsock.setblocking(0)
            self.recv = self._async_recv
            self.send = self._async_send
            self.recvfrom = self._async_recvfrom
            self.sendto = self._async_sendto
            self.accept = self._async_accept
            self.connect = self._async_connect
            if self._rsock.type & socket.SOCK_STREAM:
                self.recvall = self._async_recvall
                self.sendall = self._async_sendall
                self.recv_msg = self._async_recv_msg
                self.send_msg = self._async_send_msg
            self._asyncoro = AsynCoro.instance()
            self._notifier = _AsyncNotifier.instance()
            self._register()

    def _register(self):
        """Internal use only.
        """
        pass

    def _unregister(self):
        """Internal use only.
        """
        if self._notifier:
            self._notifier.unregister(self)
            self._notifier = None

    def close(self):
        """'close' must be called when done with socket.
        """
        self._unregister()
        if self._rsock:
            self._rsock.close()
            self._rsock = None
        self._asyncoro = None
        self._read_coro = self._write_coro = None

    def unwrap(self):
        """Get rid of AsynCoroSocket setup and return underlying socket
        object.
        """
        self._unregister()
        self._asyncoro = None
        self._notifier = None
        self._read_coro = self._write_coro = None
        sock, self._rsock = self._rsock, None
        return sock

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, trace):
        self.close()

    def setdefaulttimeout(self, timeout):
        if isinstance(timeout, (int, float)) and timeout > 0:
            self._rsock.setdefaulttimeout(timeout)
            self._default_timeout = timeout
        else:
            logger.warning('invalid timeout %s ignored', timeout)

    def getdefaulttimeout(self):
        if self._blocking:
            return self._rsock.getdefalttimeout()
        else:
            return _AsynCoroSocket._default_timeout

    def settimeout(self, timeout):
        if self._blocking:
            if timeout is None:
                pass
            elif not timeout:
                self.setblocking(0)
                self.settimeout(0.0)
            else:
                self._rsock.settimeout(timeout)
        else:
            if timeout is None:
                self.setblocking(1)
            elif isinstance(timeout, (int, float)) and timeout >= 0:
                self._timeout = timeout
                # self._notifier._del_timeout(self)
            else:
                logger.warning('invalid timeout %s ignored' % timeout)

    def gettimeout(self):
        if self._blocking:
            return self._rsock.gettimeout()
        else:
            return self._timeout

    def _timed_out(self):
        """Internal use only.
        """
        # don't clear _coro or _task; the task may complete before this
        # exception is thrown to coro
        if self._read_coro:
            self._read_coro.throw(socket.timeout('timed out'))
        if self._write_coro:
            self._write_coro.throw(socket.timeout('timed out'))

    def _async_recv(self, bufsize, *args):
        """Internal use only; use 'recv' with 'yield' instead.

        Asynchronous version of socket recv method.
        """
        def _recv(self, bufsize, *args):
            try:
                buf = self._rsock.recv(bufsize, *args)
            except ssl.SSLError as err:
                if err.args[0] != ssl.SSL_ERROR_WANT_READ:
                    raise socket.error(err)
            except:
                self._notifier.clear(self, _AsyncPoller._Read)
                self._read_task = self._read_result = None
                coro, self._read_coro = self._read_coro, None
                coro.throw(*sys.exc_info())
            else:
                self._notifier.clear(self, _AsyncPoller._Read)
                self._read_task = self._read_result = None
                coro, self._read_coro = self._read_coro, None
                coro._proceed_(buf)

        self._read_task = functools.partial(_recv, self, bufsize, *args)
        self._read_coro = self._asyncoro.cur_coro()
        self._read_coro._await_()
        self._notifier.add(self, _AsyncPoller._Read)
        if self._certfile and self._rsock.pending():
            try:
                buf = self._rsock.recv(bufsize)
            except socket.error as err:
                if err.args[0] != EWOULDBLOCK:
                    self._read_task = None
                    self._notifier.clear(self, _AsyncPoller._Read)
                    coro, self._read_coro = self._read_coro, None
                    coro.throw(*sys.exc_info())
            else:
                if buf:
                    self._read_task = None
                    self._notifier.clear(self, _AsyncPoller._Read)
                    coro, self._read_coro = self._read_coro, None
                    coro._proceed_(buf)

    def _async_recvall(self, bufsize, *args):
        """Internal use only; use 'recvall' with 'yield' instead.

        Receive exactly bufsize bytes.
        """
        def _recvall(self, view, *args):
            try:
                recvd = self._rsock.recv_into(view, len(view), *args)
            except ssl.SSLError as err:
                if err.args[0] != ssl.SSL_ERROR_WANT_READ:
                    raise socket.error(err)
            except:
                self._notifier.clear(self, _AsyncPoller._Read)
                self._read_task = self._read_result = None
                coro, self._read_coro = self._read_coro, None
                coro.throw(*sys.exc_info())
            else:
                if recvd:
                    view = view[recvd:]
                    if len(view) == 0:
                        buf = str(self._read_result)
                        self._notifier.clear(self, _AsyncPoller._Read)
                        self._read_task = self._read_result = None
                        coro, self._read_coro = self._read_coro, None
                        coro._proceed_(buf)
                    else:
                        self._read_task = functools.partial(_recvall, self, view, *args)
                else:
                    self._notifier.clear(self, _AsyncPoller._Read)
                    self._read_task = self._read_result = None
                    coro, self._read_coro = self._read_coro, None
                    coro._proceed_('')

        self._read_result = bytearray(bufsize)
        view = memoryview(self._read_result)
        self._read_task = functools.partial(_recvall, self, view, *args)
        self._read_coro = self._asyncoro.cur_coro()
        self._read_coro._await_()
        self._notifier.add(self, _AsyncPoller._Read)
        if self._certfile and self._rsock.pending():
            try:
                recvd = self._rsock.recv_into(view, bufsize)
            except socket.error as err:
                if err.args[0] != EWOULDBLOCK:
                    self._read_task = self._read_result = None
                    self._notifier.clear(self, _AsyncPoller._Read)
                    coro, self._read_coro = self._read_coro, None
                    coro.throw(*sys.exc_info())
            else:
                if recvd == bufsize:
                    buf = str(self._read_result)
                    self._read_task = self._read_result = None
                    self._notifier.clear(self, _AsyncPoller._Read)
                    coro, self._read_coro = self._read_coro, None
                    coro._proceed_(buf)
                elif recvd:
                    view = view[recvd:]
                    self._read_task = functools.partial(_recvall, self, view, *args)

    def _sync_recvall(self, bufsize, *args):
        """Internal use only; use 'recvall' instead.

        Synchronous version of async_recvall.
        """
        self._read_result = bytearray(bufsize)
        view = memoryview(self._read_result)
        while len(view) > 0:
            recvd = self._rsock.recv_into(view, *args)
            if not recvd:
                self._read_result = None
                return ''
            view = view[recvd:]
        buf, self._read_result = str(self._read_result), None
        return buf

    def _async_recvfrom(self, *args):
        """Internal use only; use 'recvfrom' with 'yield' instead.

        Asynchronous version of socket recvfrom method.
        """
        def _recvfrom(self, *args):
            try:
                res = self._rsock.recvfrom(*args)
            except:
                self._notifier.clear(self, _AsyncPoller._Read)
                self._read_task = self._read_result = None
                coro, self._read_coro = self._read_coro, None
                coro.throw(*sys.exc_info())
            else:
                self._notifier.clear(self, _AsyncPoller._Read)
                self._read_task = self._read_result = None
                coro, self._read_coro = self._read_coro, None
                coro._proceed_(res)

        self._read_task = functools.partial(_recvfrom, self, *args)
        self._read_coro = self._asyncoro.cur_coro()
        self._read_coro._await_()
        self._notifier.add(self, _AsyncPoller._Read)

    def _async_send(self, *args):
        """Internal use only; use 'send' with 'yield' instead.

        Asynchronous version of socket send method.
        """
        def _send(self, *args):
            try:
                sent = self._rsock.send(*args)
            except:
                self._notifier.clear(self, _AsyncPoller._Write)
                self._write_task = self._write_result = None
                coro, self._write_coro = self._write_coro, None
                coro.throw(*sys.exc_info())
            else:
                self._notifier.clear(self, _AsyncPoller._Write)
                self._write_task = self._write_result = None
                coro, self._write_coro = self._write_coro, None
                coro._proceed_(sent)

        self._write_task = functools.partial(_send, self, *args)
        self._write_coro = self._asyncoro.cur_coro()
        self._write_coro._await_()
        self._notifier.add(self, _AsyncPoller._Write)

    def _async_sendto(self, *args):
        """Internal use only; use 'sendto' with 'yield' instead.

        Asynchronous version of socket sendto method.
        """
        def _sendto(self, *args):
            try:
                sent = self._rsock.sendto(*args)
            except:
                self._notifier.clear(self, _AsyncPoller._Write)
                self._write_task = self._write_result = None
                coro, self._write_coro = self._write_coro, None
                coro.throw(*sys.exc_info())
            else:
                self._notifier.clear(self, _AsyncPoller._Write)
                self._write_task = self._write_result = None
                coro, self._write_coro = self._write_coro, None
                coro._proceed_(sent)

        self._write_task = functools.partial(_sendto, self, *args)
        self._write_coro = self._asyncoro.cur_coro()
        self._write_coro._await_()
        self._notifier.add(self, _AsyncPoller._Write)

    def _async_sendall(self, data):
        """Internal use only; use 'sendall' with 'yield' instead.

        Asynchronous version of socket sendall method.
        """
        def _sendall(self):
            try:
                sent = self._rsock.send(self._write_result)
                if sent < 0:
                    raise socket.error('hangup')
            except:
                self._notifier.clear(self, _AsyncPoller._Write)
                self._write_task = self._write_result = None
                coro, self._wirte_coro = self._write_coro, None
                coro.throw(*sys.exc_info())
            else:
                if sent > 0:
                    self._write_result = self._write_result[sent:]
                    if len(self._write_result) == 0:
                        self._notifier.clear(self, _AsyncPoller._Write)
                        self._write_task = self._write_result = None
                        coro, self._write_coro = self._write_coro, None
                        coro._proceed_(None)

        self._write_result = memoryview(data)
        self._write_task = functools.partial(_sendall, self)
        self._write_coro = self._asyncoro.cur_coro()
        self._write_coro._await_()
        self._notifier.add(self, _AsyncPoller._Write)

    def _sync_sendall(self, data):
        """Internal use only; use 'sendall' instead.

        Synchronous version of async_sendall.
        """
        # TODO: is socket's sendall better?
        buf = memoryview(data)
        while len(buf) > 0:
            sent = self._rsock.send(buf)
            if sent < 0:
                raise socket.error('hangup')
            buf = buf[sent:]
        return None

    def _async_accept(self):
        """Internal use only; use 'accept' with 'yield' instead.

        Asynchronous version of socket accept method. Socket in
        returned pair is asynchronous socket (instance of
        AsynCoroSocket with blocking=False).
        """
        def _accept(self):
            conn, addr = self._rsock.accept()
            self._read_task = None
            self._notifier.unregister(self)

            if self._certfile:
                def _ssl_handshake(conn, addr):
                    try:
                        conn._rsock.do_handshake()
                    except ssl.SSLError as err:
                        if err.args[0] in (ssl.SSL_ERROR_WANT_READ, ssl.SSL_ERROR_WANT_WRITE):
                            pass
                        else:
                            conn._read_task = conn._write_task = None
                            coro, conn._read_coro = conn._read_coro, None
                            conn._write_coro = None
                            conn.close()
                            coro.throw(*sys.exc_info())
                    else:
                        conn._read_task = conn._write_task = None
                        coro, conn._read_coro = conn._read_coro, None
                        conn._notifier.clear(conn)
                        coro._proceed_((conn, addr))
                conn = AsynCoroSocket(conn, blocking=False, keyfile=self._keyfile,
                                      certfile=self._certfile, ssl_version=self._ssl_version)
                conn._rsock = ssl.wrap_socket(conn._rsock, keyfile=self._keyfile,
                                              certfile=self._certfile, server_side=True,
                                              do_handshake_on_connect=False,
                                              ssl_version=self._ssl_version)
                conn._read_task = conn._write_task = functools.partial(_ssl_handshake, conn, addr)
                conn._read_coro = conn._write_coro = self._read_coro
                self._read_coro = None
                conn._notifier.add(conn, _AsyncPoller._Read | _AsyncPoller._Write)
                conn._read_task()
            else:
                coro, self._read_coro = self._read_coro, None
                conn = AsynCoroSocket(conn, blocking=False)
                coro._proceed_((conn, addr))

        self._read_task = functools.partial(_accept, self)
        self._read_coro = self._asyncoro.cur_coro()
        self._read_coro._await_()
        self._notifier.add(self, _AsyncPoller._Read)

    def _async_connect(self, *args):
        """Internal use only; use 'connect' with 'yield' instead.

        Asynchronous version of socket connect method.
        """
        def _connect(self, *args):
            err = self._rsock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if err:
                self._notifier.unregister(self)
                self._write_task = None
                coro, self._write_coro = self._write_coro, None
                coro.throw(socket.error(err))
            elif self._certfile:
                def _ssl_handshake(self):
                    try:
                        self._rsock.do_handshake()
                    except ssl.SSLError as err:
                        if err.args[0] in (ssl.SSL_ERROR_WANT_READ, ssl.SSL_ERROR_WANT_WRITE):
                            pass
                        else:
                            self._notifier.unregister(self)
                            self._read_task = self._write_task = None
                            coro, self._write_coro = self._write_coro, None
                            self._read_coro = None
                            self.close()
                            coro.throw(*sys.exc_info())
                    else:
                        self._notifier.clear(self)
                        self._read_task = self._write_task = None
                        coro, self._write_coro = self._write_coro, None
                        self._read_coro = None
                        coro._proceed_(0)

                self._rsock = ssl.wrap_socket(self._rsock, keyfile=self._keyfile,
                                              certfile=self._certfile, server_side=False,
                                              do_handshake_on_connect=False)
                self._read_task = self._write_task = functools.partial(_ssl_handshake, self)
                self._read_coro = self._write_coro
                self._notifier.add(self, _AsyncPoller._Read)
                self._write_task()
            else:
                self._write_task = None
                coro, self._write_coro = self._write_coro, None
                self._notifier.clear(self, _AsyncPoller._Write)
                coro._proceed_(0)

        self._write_task = functools.partial(_connect, self, *args)
        self._write_coro = self._asyncoro.cur_coro()
        self._write_coro._await_()
        self._notifier.add(self, _AsyncPoller._Write)
        try:
            self._rsock.connect(*args)
        except socket.error as e:
            if e.args[0] not in [EINPROGRESS, EWOULDBLOCK]:
                raise

    def _async_send_msg(self, data):
        """Internal use only; use 'send_msg' with 'yield' instead.

        Messages are tagged with length of the data, so on the
        receiving side, recv_msg knows how much data to receive.
        """
        yield self.sendall(struct.pack('>L', len(data)) + data)

    def _sync_send_msg(self, data):
        """Internal use only; use 'send_msg' instead.

        Synchronous version of async_send_msg.
        """
        return self._sync_sendall(struct.pack('>L', len(data)) + data)

    def _async_recv_msg(self):
        """Internal use only; use 'recv_msg' with 'yield' instead.

        Message is tagged with length of the payload (data). This
        method receives length of payload, then the payload and
        returns the payload.
        """
        n = struct.calcsize('>L')
        try:
            data = yield self.recvall(n)
        except socket.error as err:
            if err.args[0] == 'hangup':
                raise StopIteration('')
            else:
                raise
        if len(data) != n:
            raise StopIteration('')
        n = struct.unpack('>L', data)[0]
        assert n >= 0
        try:
            data = yield self.recvall(n)
        except socket.error as err:
            if err.args[0] == 'hangup':
                raise StopIteration('')
            else:
                raise
        if len(data) != n:
            raise StopIteration('')
        raise StopIteration(data)

    def _sync_recv_msg(self):
        """Internal use only; use 'recv_msg' instead.

        Synchronous version of async_recv_msg.
        """
        n = struct.calcsize('>L')
        try:
            data = self._sync_recvall(n)
        except socket.error as err:
            if err.args[0] == 'hangup':
                return ''
            else:
                raise
        if len(data) != n:
            return ''
        n = struct.unpack('>L', data)[0]
        assert n >= 0
        try:
            data = self._sync_recvall(n)
        except socket.error as err:
            if err.args[0] == 'hangup':
                return ''
            else:
                raise
        if len(data) != n:
            return ''
        return data

    def create_connection(self, host_port, timeout=None, source_address=None):
        if timeout is not None:
            self.settimeout(timeout)
        if source_address is not None:
            self._rsock.bind(source_address)
        yield self.connect(host_port)

if platform.system() == 'Windows':
    # use IOCP if pywin32 (http://pywin32.sf.net) is installed
    try:
        import win32file
        import win32event
        import pywintypes
        import winerror
    except:
        logger.warning('Could not load pywin32 for I/O Completion Ports; ' \
                       'using inefficient polling for sockets')
    else:
        # for UDP we need 'select' polling (pywin32 doesn't yet support
        # UDP); _AsyncPoller below is combination of the other
        # _AsyncPoller for epoll/poll/kqueue/select and _SelectNotifier
        # below. (Un)fortunately, most of it is duplicate code
        class _AsyncPoller(object):
            """Internal use only.
            """

            __metaclass__ = MetaSingleton
            __instance = None

            _Read = 0x1
            _Write = 0x2
            _Error = 0x4

            @classmethod
            def instance(cls):
                # assert cls.__instance is not None
                return cls.__instance

            def __init__(self, iocp_notifier):
                if not hasattr(self, 'poller'):
                    self.__class__.__instance = self
                    self._fds = {}
                    self._events = {}
                    self._lock = threading.Lock()
                    self.polling = False
                    self._terminate = False
                    self.rset = set()
                    self.wset = set()
                    self.xset = set()
                    self.iocp_notifier = iocp_notifier
                    self.cmd_rsock, self.cmd_wsock = _AsyncPoller._socketpair()
                    self.cmd_rsock.setblocking(0)
                    self.cmd_wsock.setblocking(0)
                    self.poller = select.select
                    self.poll_thread = threading.Thread(target=self.poll)
                    self.poll_thread.daemon = True
                    self.poll_thread.start()

            def unregister(self, fd, update=True):
                fid = fd._fileno
                if fd._timeout:
                    self.iocp_notifier._del_timeout(fd)
                self._lock.acquire()
                if update:
                    if self._fds.pop(fid, None) != fd:
                        self._lock.release()
                        logger.debug('fd %s is not registered', fd._fileno)
                        return
                    event = self._events.pop(fid, 0)
                else:
                    event = self._events.get(fd, 0)
                self._lock.release()
                if event & _AsyncPoller._Read:
                    self.rset.discard(fid)
                if event & _AsyncPoller._Write:
                    self.wset.discard(fid)
                if event & _AsyncPoller._Error:
                    self.xset.discard(fid)
                if update and self.polling:
                    self.cmd_wsock.send('u')

            def add(self, fd, event):
                fid = fd._fileno
                if fd._timeout:
                    self.iocp_notifier._del_timeout(fd)
                cur_event = self._events.get(fid, 0)
                if cur_event & _AsyncPoller._Read:
                    self.rset.discard(fid)
                if cur_event & _AsyncPoller._Write:
                    self.wset.discard(fid)
                if cur_event & _AsyncPoller._Error:
                    self.xset.discard(fid)
                event |= cur_event
                self._events[fid] = event
                self._fds[fid] = fd
                if event:
                    if event & _AsyncPoller._Read:
                        self.rset.add(fid)
                    if event & _AsyncPoller._Write:
                        self.wset.add(fid)
                    if event & _AsyncPoller._Error:
                        self.xset.add(fid)
                    if fd._timeout:
                        self.iocp_notifier._add_timeout(fd)
                        self.iocp_notifier.interrupt(fd._timeout)
                if self.polling:
                    self.cmd_wsock.send('m')

            def clear(self, fd, event=0):
                fid = fd._fileno
                cur_event = self._events.get(fid, 0)
                if cur_event:
                    if cur_event & _AsyncPoller._Read:
                        self.rset.discard(fid)
                    if cur_event & _AsyncPoller._Write:
                        self.wset.discard(fid)
                    if cur_event & _AsyncPoller._Error:
                        self.xset.discard(fid)
                    if event:
                        cur_event &= ~event
                    else:
                        cur_event = 0
                    self._events[fid] = cur_event
                    if cur_event:
                        if cur_event & _AsyncPoller._Read:
                            self.rset.add(fid)
                        if cur_event & _AsyncPoller._Write:
                            self.wset.add(fid)
                        if cur_event & _AsyncPoller._Error:
                            self.xset.add(fid)
                    elif fd._timeout:
                        self.iocp_notifier._del_timeout(fd)
                    if self.polling:
                        self.cmd_wsock.send('m')

            def poll(self):
                self.cmd_rsock = AsynCoroSocket(self.cmd_rsock)
                setattr(self.cmd_rsock, '_read_task', lambda: self.cmd_rsock._rsock.recv(128))
                self.add(self.cmd_rsock, _AsyncPoller._Read)
                while True:
                    self.polling = True
                    rlist, wlist, xlist = self.poller(self.rset, self.wset, self.xset)
                    self.polling = False
                    if self._terminate:
                        break
                    events = {}
                    for fid in rlist:
                        events[fid] = _AsyncPoller._Read
                    for fid in wlist:
                        events[fid] = events.get(fid, 0) | _AsyncPoller._Write
                    for fid in xlist:
                        events[fid] = events.get(fid, 0) | _AsyncPoller._Error

                    self._lock.acquire()
                    events = [(self._fds.get(fid, None), event) \
                              for (fid, event) in events.iteritems()]
                    self._lock.release()
                    iocp_notify = False
                    for fd, event in events:
                        if fd is None:
                            logger.debug('invalid fd')
                            continue
                        if event & _AsyncPoller._Read:
                            if fd._read_task:
                                if fd != self.cmd_rsock:
                                    iocp_notify = True
                                fd._read_task()
                            else:
                                logger.debug('fd %s is not registered for reading!', fd._fileno)
                        if event & _AsyncPoller._Write:
                            if fd._write_task:
                                iocp_notify = True
                                fd._write_task()
                            else:
                                logger.debug('fd %s is not registered for writing!', fd._fileno)
                        if event & _AsyncPoller._Error:
                            if fd._read_coro:
                                fd._read_coro.throw(socket.error(_AsyncPoller._Error))
                            if fd._write_coro:
                                fd._write_coro.throw(socket.error(_AsyncPoller._Error))
                    if iocp_notify:
                        self.iocp_notifier.interrupt()

                self.rset = set()
                self.wset = set()
                self.xset = set()
                self.cmd_rsock.close()
                self.cmd_wsock.close()

            def terminate(self):
                self._terminate = True
                self.cmd_wsock.send('x')
                self.poll_thread.join()

            @staticmethod
            def _socketpair():
                srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv_sock.bind(('127.0.0.1', 0))
                srv_sock.listen(1)

                sock1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                conn_thread = threading.Thread(target=lambda sock, addr_port: sock.connect(addr_port),
                                               args=(sock1, srv_sock.getsockname()))
                conn_thread.daemon = True
                conn_thread.start()
                sock2, caddr = srv_sock.accept()
                srv_sock.close()
                return (sock1, sock2)

        class _AsyncNotifier(object):
            """Internal use only.
            """

            __metaclass__ = MetaSingleton
            __instance = None

            _Block = win32event.INFINITE

            @classmethod
            def instance(cls, *args, **kwargs):
                if cls.__instance is None:
                    cls.__instance = cls(*args, **kwargs)
                return cls.__instance

            def __init__(self):
                if not hasattr(self, 'iocp'):
                    self.__class__.__instance = self
                    self.iocp = win32file.CreateIoCompletionPort(win32file.INVALID_HANDLE_VALUE,
                                                                 None, 0, 0)
                    self._timeouts = []
                    self._timeout_fds = []
                    self.poll_timeout = 0
                    self._lock = threading.Lock()
                    self.async_poller = _AsyncPoller(self)
                    self.cmd_rsock, self.cmd_wsock = _AsyncPoller._socketpair()
                    self.cmd_wsock.setblocking(0)
                    self.cmd_rsock = AsynCoroSocket(self.cmd_rsock)
                    self.cmd_rsock_buf = win32file.AllocateReadBuffer(128)
                    self.cmd_rsock._read_overlap.object = self.cmd_rsock_recv
                    err, n = win32file.WSARecv(self.cmd_rsock._fileno, self.cmd_rsock_buf,
                                               self.cmd_rsock._read_overlap, 0)
                    if err and err != winerror.ERROR_IO_PENDING:
                        logger.warning('WSARecv error: %s', err)

            def cmd_rsock_recv(self, err, n):
                if n == 0:
                    err = winerror.ERROR_CONNECTION_INVALID
                if err:
                    logger.warning('iocp cmd recv error: %s', err)
                err, n = win32file.WSARecv(self.cmd_rsock._fileno, self.cmd_rsock_buf,
                                           self.cmd_rsock._read_overlap, 0)
                if err and err != winerror.ERROR_IO_PENDING:
                    logger.warning('WSARecv error: %s', err)

            def interrupt(self, timeout=None):
                if timeout is None:
                    self.cmd_wsock.send('i')
                elif self.poll_timeout == _AsyncNotifier._Block or timeout < self.poll_timeout:
                    self.cmd_wsock.send('I')

            def register(self, fd, event=0):
                win32file.CreateIoCompletionPort(fd._fileno, self.iocp, 1, 0)

            def unregister(self, fd):
                pass

            def modify(self, fd, event):
                pass

            def poll(self, timeout):
                self._lock.acquire()
                if timeout == 0:
                    self.poll_timeout = 0
                elif self._timeouts:
                    self.poll_timeout = self._timeouts[0] - _time()
                    if self.poll_timeout < 0.001:
                        self.poll_timeout = 0
                    elif timeout is not None:
                        self.poll_timeout = min(timeout, self.poll_timeout)
                elif timeout is None:
                    self.poll_timeout = _AsyncNotifier._Block
                else:
                    self.poll_timeout = timeout
                timeout = self.poll_timeout
                self._lock.release()
                if timeout and timeout != _AsyncNotifier._Block:
                    timeout = int(timeout * 1000)
                err, n, key, overlap = win32file.GetQueuedCompletionStatus(self.iocp, timeout)
                while err != winerror.WAIT_TIMEOUT:
                    if overlap and overlap.object:
                        overlap.object(err, n)
                    else:
                        logger.warning('invalid overlap!')
                    err, n, key, overlap = win32file.GetQueuedCompletionStatus(self.iocp, 0)
                self.poll_timeout = 0
                if timeout == 0:
                    now = _time()
                    self._lock.acquire()
                    while self._timeouts and self._timeouts[0] <= now:
                        fd = self._timeout_fds[0]
                        if fd._timeout_id == self._timeouts[0]:
                            fd._timed_out()
                            fd._timeout_id = None
                        del self._timeouts[0]
                        del self._timeout_fds[0]
                    self._lock.release()

            def _add_timeout(self, fd):
                if fd._timeout:
                    timeout = _time() + fd._timeout
                    self._lock.acquire()
                    i = bisect_left(self._timeouts, timeout)
                    self._timeouts.insert(i, timeout)
                    self._timeout_fds.insert(i, fd)
                    fd._timeout_id = timeout
                    self._lock.release()
                else:
                    fd._timeout_id = None

            def _del_timeout(self, fd):
                if fd._timeout_id:
                    self._lock.acquire()
                    i = bisect_left(self._timeouts, fd._timeout_id)
                    # in case of identical timeouts (unlikely?), search for
                    # correct index where fd is
                    while i < len(self._timeouts):
                        if self._timeout_fds[i] == fd:
                            # assert fd._timeout_id == self._timeouts[i]
                            del self._timeouts[i]
                            del self._timeout_fds[i]
                            fd._timeout_id = None
                            break
                        if fd._timeout_id != self._timeouts[i]:
                            logger.warning('fd %s with %s is not found',
                                           fd._fileno, fd._timeout_id)
                            break
                        i += 1
                    self._lock.release()

            def terminate(self):
                self.async_poller.terminate()
                self.cmd_rsock.close()
                self.cmd_wsock.close()
                win32file.CloseHandle(self.iocp)
                self.iocp = None
                self.cmd_rsock_buf = None

        class AsynCoroSocket(_AsynCoroSocket):
            """AsynCoroSocket with I/O Completion Ports (under
            Windows). See _AsynCoroSocket above for more details.  UDP
            traffic is handled by _AsyncPoller.
            """

            __slots__ = _AsynCoroSocket.__slots__ + ('_read_overlap', '_write_overlap')

            def __init__(self, *args, **kwargs):
                self._read_overlap = None
                self._write_overlap = None
                _AsynCoroSocket.__init__(self, *args, **kwargs)

            def _register(self):
                if not self._blocking:
                    if self._rsock.type & socket.SOCK_STREAM:
                        self._read_overlap = pywintypes.OVERLAPPED()
                        self._write_overlap = pywintypes.OVERLAPPED()
                        self._notifier.register(self)
                    else:
                        self._notifier = _AsyncPoller.instance()
                else:
                    _AsynCoroSocket._register(self)

            def _unregister(self):
                if self._notifier:
                    self._notifier.unregister(self)
                    if self._rsock.type & socket.SOCK_STREAM:
                        if self._read_overlap or self._write_overlap:
                            win32file.CancelIo(self._fileno)
                        else:
                            self._read_overlap = None
                            self._write_overlap = None
                    self._notifier = None

            def setblocking(self, blocking):
                _AsynCoroSocket.setblocking(self, blocking)
                if not self._blocking and self._rsock.type & socket.SOCK_STREAM:
                    self.recv = self._iocp_recv
                    self.send = self._iocp_send
                    self.recvall = self._iocp_recvall
                    self.sendall = self._iocp_sendall
                    self.connect = self._iocp_connect
                    self.accept = self._iocp_accept

            def _timed_out(self):
                if self._read_coro:
                    self._read_coro.throw(socket.timeout('timed out'))

            def _iocp_recv(self, bufsize, *args):
                """Internal use only; use 'recv' with 'yield' instead.
                """
                def _recv(self, err, n):
                    if self._timeout and self._notifier:
                        self._notifier._del_timeout(self)
                    if err or n == 0:
                        self._read_overlap.object = self._read_result = None
                        if err == winerror.ERROR_OPERATION_ABORTED:
                            self._read_overlap = None
                        else:
                            if not err:
                                err = winerror.ERROR_CONNECTION_INVALID
                            coro, self._read_coro = self._read_coro, None
                            if coro:
                                if err == winerror.ERROR_CONNECTION_INVALID:
                                    coro._proceed_('')
                                else:
                                    coro.throw(socket.error(err))
                    else:
                        buf = self._read_result[:n]
                        self._read_overlap.object = self._read_result = None
                        coro, self._read_coro = self._read_coro, None
                        if coro:
                            coro._proceed_(buf)

                self._read_result = win32file.AllocateReadBuffer(bufsize)
                self._read_overlap.object = functools.partial(_recv, self)
                self._read_coro = self._asyncoro.cur_coro()
                self._read_coro._await_()
                if self._timeout:
                    self._notifier._add_timeout(self)
                err, n = win32file.WSARecv(self._fileno, self._read_result, self._read_overlap, 0)
                if err and err != winerror.ERROR_IO_PENDING:
                    self._read_overlap.object = self._read_result = self._read_coro = None
                    raise socket.error(err)

            def _iocp_send(self, buf, *args):
                """Internal use only; use 'send' with 'yield' instead.
                """
                def _send(self, err, n):
                    if self._timeout and self._notifier:
                        self._notifier._del_timeout(self)
                    if err or n == 0:
                        self._write_overlap.object = self._write_result = None
                        if err == winerror.ERROR_OPERATION_ABORTED:
                            self._write_overlap = None
                        else:
                            if not err:
                                err = winerror.ERROR_CONNECTION_INVALID
                            coro, self._write_coro = self._write_coro, None
                            if coro:
                                coro.throw(socket.error(err))
                    else:
                        self._write_overlap.object = self._write_result = None
                        coro, self._write_coro = self._write_coro, None
                        if coro:
                            coro._proceed_(n)

                self._write_overlap.object = functools.partial(_send, self)
                self._write_coro = self._asyncoro.cur_coro()
                self._write_coro._await_()
                if self._timeout:
                    self._notifier._add_timeout(self)
                err, n = win32file.WSASend(self._fileno, buf, self._write_overlap, 0)
                if err and err != winerror.ERROR_IO_PENDING:
                    self._write_overlap.object = self._write_result = self._write_coro = None
                    raise socket.error(err)

            def _iocp_recvall(self, bufsize, *args):
                """Internal use only; use 'recvall' with 'yield' instead.
                """
                def _recvall(self, pending, buf, err, n):
                    if err or n == 0:
                        if self._timeout and self._notifier:
                            self._notifier._del_timeout(self)
                        self._read_overlap.object = self._read_result = None
                        if err == winerror.ERROR_OPERATION_ABORTED:
                            self._read_overlap = None
                        else:
                            if not err:
                                err = winerror.ERROR_CONNECTION_INVALID
                            coro, self._read_coro = self._read_coro, None
                            if coro:
                                if err == winerror.ERROR_CONNECTION_INVALID:
                                    coro._proceed_('')
                                else:
                                    coro.throw(socket.error(err))
                    else:
                        self._read_result.append(buf[:n])
                        pending -= n
                        if pending == 0:
                            buf = ''.join(self._read_result)
                            if self._timeout and self._notifier:
                                self._notifier._del_timeout(self)
                            self._read_overlap.object = self._read_result = None
                            coro, self._read_coro = self._read_coro, None
                            if coro:
                                coro._proceed_(buf)
                        else:
                            buf = win32file.AllocateReadBuffer(min(pending, 1048576))
                            self._read_overlap.object = functools.partial(_recvall, self,
                                                                          pending, buf)
                            err, n = win32file.WSARecv(self._fileno, buf, self._read_overlap, 0)
                            if err and err != winerror.ERROR_IO_PENDING:
                                if self._timeout and self._notifier:
                                    self._notifier._del_timeout(self)
                                self._read_overlap.object = self._read_result = None
                                coro, self._read_coro = self._read_coro, None
                                if coro:
                                    coro.throw(socket.error(err))

                self._read_result = []
                buf = win32file.AllocateReadBuffer(min(bufsize, 1048576))
                self._read_overlap.object = functools.partial(_recvall, self, bufsize, buf)
                self._read_coro = self._asyncoro.cur_coro()
                self._read_coro._await_()
                if self._timeout:
                    self._notifier._add_timeout(self)
                err, n = win32file.WSARecv(self._fileno, buf, self._read_overlap, 0)
                if err and err != winerror.ERROR_IO_PENDING:
                    self._read_overlap.object = self._read_result = self._read_coro = None
                    raise socket.error(err)

            def _iocp_sendall(self, data):
                """Internal use only; use 'sendall' with 'yield' instead.
                """
                def _sendall(self, err, n):
                    if err or n == 0:
                        if self._timeout and self._notifier:
                            self._notifier._del_timeout(self)
                        self._write_overlap.object = self._write_result = None
                        if err == winerror.ERROR_OPERATION_ABORTED:
                            self._write_overlap = None
                        else:
                            if not err:
                                err = winerror.ERROR_CONNECTION_INVALID
                            coro, self._write_coro = self._write_coro, None
                            if coro:
                                coro.throw(socket.error(err))
                    else:
                        self._write_result = buffer(self._write_result, n)
                        if len(self._write_result) == 0:
                            if self._timeout and self._notifier:
                                self._notifier._del_timeout(self)
                            self._write_overlap.object = self._write_result = None
                            coro, self._write_coro = self._write_coro, None
                            if coro:
                                coro._proceed_(0)
                        else:
                            err, n = win32file.WSASend(self._fileno, self._write_result,
                                                       self._write_overlap, 0)
                            if err and err != winerror.ERROR_IO_PENDING:
                                if self._timeout and self._notifier:
                                    self._notifier._del_timeout(self)
                                self._write_overlap.object = self._write_result = None
                                coro, self._write_coro = self._write_coro, None
                                if coro:
                                    coro.throw(socket.error(err))

                self._write_result = buffer(data, 0)
                self._write_overlap.object = functools.partial(_sendall, self)
                self._write_coro = self._asyncoro.cur_coro()
                self._write_coro._await_()
                if self._timeout:
                    self._notifier._add_timeout(self)
                err, n = win32file.WSASend(self._fileno, self._write_result, self._write_overlap, 0)
                if err and err != winerror.ERROR_IO_PENDING:
                    self._write_overlap.object = self._write_result = self._write_coro = None
                    raise socket.error(err)

            def _iocp_connect(self, host_port):
                """Internal use only; use 'connect' with 'yield' instead.
                """
                def _connect(self, err, n):
                    def _ssl_handshake(self, err, n):
                        try:
                            self._rsock.do_handshake()
                        except ssl.SSLError as err:
                            if err.args[0] == ssl.SSL_ERROR_WANT_READ:
                                err, n = win32file.WSARecv(self._fileno, self._read_result,
                                                           self._read_overlap, 0)
                            elif err.args[0] == ssl.SSL_ERROR_WANT_WRITE:
                                err, n = win32file.WSASend(self._fileno, '', self._read_overlap, 0)
                            else:
                                raise socket.error(err)
                        except:
                            if self._timeout and self._notifier:
                                self._notifier._del_timeout(self)
                            self._read_overlap.object = self._read_result = None
                            self.close()
                            if err == winerror.ERROR_OPERATION_ABORTED:
                                self._read_overlap = None
                            else:
                                coro, self._read_coro = self._read_coro, None
                                if coro:
                                    coro.throw(socket.error(err))
                        else:
                            if self._timeout and self._notifier:
                                self._notifier._del_timeout(self)
                            self._read_overlap.object = self._read_result = None
                            coro, self._read_coro = self._read_coro, None
                            if coro:
                                coro._proceed_(0)

                    if err:
                        if self._timeout and self._notifier:
                            self._notifier._del_timeout(self)
                        self._read_overlap.object = self._read_result = None
                        if err == winerror.ERROR_OPERATION_ABORTED:
                            self._read_overlap = None
                        else:
                            coro, self._read_coro = self._read_coro, None
                            if coro:
                                coro.throw(socket.error(err))
                    else:
                        self._rsock.setsockopt(socket.SOL_SOCKET, win32file.SO_UPDATE_CONNECT_CONTEXT, '')
                        if self._certfile:
                            self._rsock = ssl.wrap_socket(self._rsock, keyfile=self._keyfile,
                                                          certfile=self._certfile, server_side=False,
                                                          do_handshake_on_connect=False)
                            self._read_result = win32file.AllocateReadBuffer(0)
                            self._read_overlap.object = functools.partial(_ssl_handshake, self)
                            self._read_overlap.object(None, 0)
                        else:
                            if self._timeout and self._notifier:
                                self._notifier._del_timeout(self)
                            self._read_overlap.object = self._read_result = None
                            coro, self._read_coro = self._read_coro, None
                            if coro:
                                coro._proceed_(0)

                # ConnectEX requires socket to be bound!
                try:
                    self._rsock.bind(('0.0.0.0', 0))
                except socket.error as exc:
                    if exc[0] != EINVAL:
                        raise
                self._read_overlap.object = functools.partial(_connect, self)
                self._read_coro = self._asyncoro.cur_coro()
                self._read_coro._await_()
                if self._timeout:
                    self._notifier._add_timeout(self)
                err, n = win32file.ConnectEx(self._rsock, host_port, self._read_overlap)
                if err and err != winerror.ERROR_IO_PENDING:
                    self._read_overlap.object = self._read_result = self._read_coro = None
                    raise socket.error(err)

            def _iocp_accept(self):
                """Internal use only; use 'accept' with 'yield'
                instead. Socket in returned pair is asynchronous
                socket (instance of AsynCoroSocket with blocking=False).
                """
                def _accept(self, conn, err, n):
                    def _ssl_handshake(self, conn, addr, err, n):
                        try:
                            conn._rsock.do_handshake()
                        except ssl.SSLError as err:
                            if err.args[0] == ssl.SSL_ERROR_WANT_READ:
                                err, n = win32file.WSARecv(conn._fileno, self._read_result,
                                                           self._read_overlap, 0)
                            elif err.args[0] == ssl.SSL_ERROR_WANT_WRITE:
                                err, n = win32file.WSASend(conn._fileno, '', self._read_overlap, 0)
                            else:
                                raise socket.error(err)
                        except:
                            if self._timeout and self._notifier:
                                self._notifier._del_timeout(self)
                            self._read_overlap.object = self._read_result = None
                            conn.close()
                            if err == winerror.ERROR_OPERATION_ABORTED:
                                self._read_overlap = None
                            else:
                                coro, self._read_coro = self._read_coro, None
                                if coro:
                                    coro.throw(socket.error(err))
                        else:
                            if self._timeout and self._notifier:
                                self._notifier._del_timeout(self)
                            self._read_overlap.object = self._read_result = None
                            coro, self._read_coro = self._read_coro, None
                            if coro:
                                coro._proceed_((conn, addr))

                    if err:
                        if self._timeout and self._notifier:
                            self._notifier._del_timeout(self)
                        self._read_overlap.object = self._read_result = None
                        if err == winerror.ERROR_OPERATION_ABORTED:
                            self._read_overlap = None
                        else:
                            coro, self._read_coro = self._read_coro, None
                            if coro:
                                coro.throw(socket.error(err))
                    else:
                        family, laddr, raddr = win32file.GetAcceptExSockaddrs(conn, self._read_result)
                        # TODO: unpack raddr if family != AF_INET
                        conn._rsock.setsockopt(socket.SOL_SOCKET, win32file.SO_UPDATE_ACCEPT_CONTEXT,
                                               struct.pack('P', self._fileno))
                        if self._certfile:
                            conn._rsock = ssl.wrap_socket(conn._rsock, keyfile=self._keyfile,
                                                          certfile=self._certfile, server_side=True,
                                                          do_handshake_on_connect=False,
                                                          ssl_version=self._ssl_version)
                            self._read_result = win32file.AllocateReadBuffer(0)
                            self._read_overlap.object = functools.partial(_ssl_handshake, self,
                                                                          conn, raddr)
                            self._read_overlap.object(None, 0)
                        else:
                            if self._timeout and self._notifier:
                                self._notifier._del_timeout(self)
                            self._read_overlap.object = self._read_result = None
                            coro, self._read_coro = self._read_coro, None
                            if coro:
                                coro._proceed_((conn, raddr))

                sock = socket.socket(self._rsock.family, self._rsock.type, self._rsock.proto)
                conn = AsynCoroSocket(sock, keyfile=self._keyfile, certfile=self._certfile,
                                      ssl_version=self._ssl_version)
                self._read_result = win32file.AllocateReadBuffer(win32file.CalculateSocketEndPointSize(sock))
                self._read_overlap.object = functools.partial(_accept, self, conn)
                self._read_coro = self._asyncoro.cur_coro()
                self._read_coro._await_()
                if self._timeout:
                    self._notifier._add_timeout(self)
                err = win32file.AcceptEx(self._fileno, conn._fileno, self._read_result,
                                         self._read_overlap)
                if err and err != winerror.ERROR_IO_PENDING:
                    self._read_overlap.object = self._read_result = self._read_coro = None
                    raise socket.error(err)

if not isinstance(getattr(sys.modules[__name__], '_AsyncNotifier', None), MetaSingleton):
    class _AsyncPoller(object):
        """Internal use only.
        """

        __metaclass__ = MetaSingleton
        __instance = None

        _Read = None
        _Write = None
        _Hangup = None
        _Error = None

        _Block = None

        @classmethod
        def instance(cls, *args, **kwargs):
            if cls.__instance is None:
                cls.__instance = cls(*args, **kwargs)
            return cls.__instance

        def __init__(self):
            if self.__class__.__instance is None:
                self.__class__.__instance = self
                self.timeout_multiplier = 1

                if hasattr(select, 'epoll'):
                    logger.debug('poller: epoll')
                    self._poller = select.epoll()
                    _AsyncPoller._Read = select.EPOLLIN | select.EPOLLPRI
                    _AsyncPoller._Write = select.EPOLLOUT
                    _AsyncPoller._Hangup = select.EPOLLHUP
                    _AsyncPoller._Error = select.EPOLLHUP | select.EPOLLERR
                    _AsyncPoller._Block = -1
                elif hasattr(select, 'kqueue'):
                    logger.debug('poller: kqueue')
                    self._poller = _KQueueNotifier()
                    # kqueue filter values are negative numbers so using
                    # them as flags won't work, so define them as necessary
                    _AsyncPoller._Read = 0x01
                    _AsyncPoller._Write = 0x02
                    _AsyncPoller._Hangup = 0x04
                    _AsyncPoller._Error = 0x08
                    _AsyncPoller._Block = None
                elif hasattr(select, 'devpoll'):
                    logger.debug('poller: devpoll')
                    self._poller = select.devpoll()
                    _AsyncPoller._Read = select.POLLIN | select.POLLPRI
                    _AsyncPoller._Write = select.POLLOUT
                    _AsyncPoller._Hangup = select.POLLHUP
                    _AsyncPoller._Error = select.POLLHUP | select.POLLERR
                    _AsyncPoller._Block = -1
                    self.timeout_multiplier = 1000
                elif hasattr(select, 'poll'):
                    logger.debug('poller: poll')
                    self._poller = select.poll()
                    _AsyncPoller._Read = select.POLLIN | select.POLLPRI
                    _AsyncPoller._Write = select.POLLOUT
                    _AsyncPoller._Hangup = select.POLLHUP
                    _AsyncPoller._Error = select.POLLHUP | select.POLLERR
                    _AsyncPoller._Block = -1
                    self.timeout_multiplier = 1000
                else:
                    logger.debug('poller: select')
                    self._poller = _SelectNotifier()
                    _AsyncPoller._Read = 0x01
                    _AsyncPoller._Write = 0x02
                    _AsyncPoller._Hangup = 0x04
                    _AsyncPoller._Error = 0x08
                    _AsyncPoller._Block = None

                self._fds = {}
                self._events = {}
                self._timeouts = []
                self._timeout_fds = []
                self.cmd_rsock, self.cmd_wsock = _AsyncPoller._socketpair()
                self.cmd_wsock.setblocking(0)
                self.cmd_rsock = AsynCoroSocket(self.cmd_rsock)
                setattr(self.cmd_rsock, '_read_task', lambda: self.cmd_rsock._rsock.recv(128))
                self.add(self.cmd_rsock, _AsyncPoller._Read)

        def interrupt(self):
            self.cmd_wsock.send('I')

        def poll(self, timeout):
            """Calls 'task' method of registered fds when there is a
            read/write event for it.
            """

            if timeout == 0:
                poll_timeout = timeout
            elif self._timeouts:
                poll_timeout = self._timeouts[0] - _time()
                if poll_timeout < 0.001:
                    poll_timeout = 0
                elif timeout is not None:
                    poll_timeout = min(timeout, poll_timeout)
            elif timeout is None:
                poll_timeout = _AsyncPoller._Block
            else:
                poll_timeout = timeout
            if poll_timeout and poll_timeout != _AsyncPoller._Block:
                poll_timeout *= self.timeout_multiplier

            try:
                events = self._poller.poll(poll_timeout)
            except:
                logger.debug('poll failed')
                logger.debug(traceback.format_exc())
                # prevent tight loops
                time.sleep(5)
                return
            try:
                for fileno, event in events:
                    fd = self._fds.get(fileno, None)
                    if fd is None:
                        if event != _AsyncPoller._Hangup:
                            logger.debug('invalid fd for event %s', event)
                        continue
                    if event & _AsyncPoller._Hangup:
                        self.unregister(fd)
                        if fd._read_coro:
                            fd._read_coro.throw(socket.error('hangup'))
                        if fd._write_coro:
                            fd._write_coro.throw(socket.error('hangup'))
                        continue
                    if event & _AsyncPoller._Read:
                        if fd._read_task is None:
                            logger.debug('fd %s is not registered for read!', fd._fileno)
                        else:
                            fd._read_task()
                    if event & _AsyncPoller._Write:
                        if fd._write_task is None:
                            logger.debug('fd %s is not registered for write!', fd._fileno)
                        else:
                            fd._write_task()
            except:
                logger.debug(traceback.format_exc())

            if timeout == 0:
                now = _time()
                while self._timeouts and self._timeouts[0] <= now:
                    fd = self._timeout_fds[0]
                    if fd._timeout_id == self._timeouts[0]:
                        fd._timed_out()
                        fd._timeout_id = None
                    del self._timeouts[0]
                    del self._timeout_fds[0]

        def terminate(self):
            self.cmd_wsock.close()
            self.cmd_rsock.close()
            if hasattr(self._poller, 'terminate'):
                self._poller.terminate()
            else:
                for fd in self._fds.itervalues():
                    try:
                        self._poller.unregister(fd._fileno)
                    except:
                        logger.warning('unregister of %s failed with %s',
                                       fd._fileno, traceback.format_exc())
            self._poller = None
            self._timeouts = []
            self._timeout_fds = []
            self._fds = {}

        def _add_timeout(self, fd):
            if fd._timeout:
                timeout = _time() + fd._timeout
                i = bisect_left(self._timeouts, timeout)
                self._timeouts.insert(i, timeout)
                self._timeout_fds.insert(i, fd)
                fd._timeout_id = timeout
            else:
                fd._timeout_id = None

        def _del_timeout(self, fd):
            if fd._timeout_id:
                i = bisect_left(self._timeouts, fd._timeout_id)
                # in case of identical timeouts (unlikely?), search for
                # correct index where fd is
                while i < len(self._timeouts):
                    if self._timeout_fds[i] == fd:
                        # assert fd._timeout_id == self._timeouts[i]
                        del self._timeouts[i]
                        del self._timeout_fds[i]
                        fd._timeout_id = None
                        break
                    if fd._timeout_id != self._timeouts[i]:
                        logger.warning('fd %s with %s is not found', fd._fileno, fd._timeout_id)
                        break
                    i += 1

        def unregister(self, fd):
            if self._fds.pop(fd._fileno, None) is None:
                # logger.debug('fd %s is not registered', fd._fileno)
                return
            self._events.pop(fd._fileno, None)
            self._poller.unregister(fd._fileno)
            self._del_timeout(fd)

        def add(self, fd, event):
            cur_event = self._events.get(fd._fileno, None)
            if cur_event is None:
                self._fds[fd._fileno] = fd
                self._events[fd._fileno] = event
                self._poller.register(fd._fileno, event)
            else:
                event |= cur_event
                self._events[fd._fileno] = event
                self._poller.modify(fd._fileno, event)
            self._add_timeout(fd)

        def clear(self, fd, event=0):
            cur_event = self._events.get(fd._fileno, None)
            if cur_event:
                if event:
                    cur_event &= ~event
                else:
                    cur_event = 0
                self._events[fd._fileno] = cur_event
                self._poller.modify(fd._fileno, cur_event)
                if not cur_event:
                    self._del_timeout(fd)

        @staticmethod
        def _socketpair():
            if hasattr(socket, 'socketpair'):
                return socket.socketpair()
            srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv_sock.bind(('127.0.0.1', 0))
            srv_sock.listen(1)

            sock1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn_thread = threading.Thread(target=lambda sock, addr_port: sock.connect(addr_port),
                                           args=(sock1, srv_sock.getsockname()))
            conn_thread.daemon = True
            conn_thread.start()
            sock2, caddr = srv_sock.accept()
            srv_sock.close()
            return (sock1, sock2)

    class _KQueueNotifier(object):
        """Internal use only.
        """

        __metaclass__ = MetaSingleton

        def __init__(self):
            if not hasattr(self, 'poller'):
                self.poller = select.kqueue()
                self.events = {}

        def register(self, fid, event):
            self.events[fid] = event
            self.update(fid, event, select.KQ_EV_ADD)

        def unregister(self, fid):
            event = self.events.pop(fid, None)
            if event is not None:
                self.update(fid, event, select.KQ_EV_DELETE)

        def modify(self, fid, event):
            self.unregister(fid)
            self.register(fid, event)

        def update(self, fid, event, flags):
            if event & _AsyncPoller._Read:
                self.poller.control([select.kevent(fid, filter=select.KQ_FILTER_READ, flags=flags)], 0)
            if event & _AsyncPoller._Write:
                self.poller.control([select.kevent(fid, filter=select.KQ_FILTER_WRITE, flags=flags)], 0)

        def poll(self, timeout):
            kevents = self.poller.control(None, 500, timeout)
            events = [(kevent.ident,
                       _AsyncPoller._Read if kevent.filter == select.KQ_FILTER_READ else \
                           _AsyncPoller._Write if kevent.filter == select.KQ_FILTER_WRITE else 0 | \
                           _AsyncPoller._Hangup if kevent.flags == select.KQ_EV_EOF else \
                           _AsyncPoller._Error if kevent.flags == select.KQ_EV_ERROR else 0) \
                          for kevent in kevents]
            return events

    class _SelectNotifier(object):
        """Internal use only.
        """

        __metaclass__ = MetaSingleton

        def __init__(self):
            if not hasattr(self, 'poller'):
                self.poller = select.select
                self.rset = set()
                self.wset = set()
                self.xset = set()

        def register(self, fid, event):
            if event:
                if event & _AsyncPoller._Read:
                    self.rset.add(fid)
                if event & _AsyncPoller._Write:
                    self.wset.add(fid)
                if event & _AsyncPoller._Error:
                    self.xset.add(fid)

        def unregister(self, fid):
            self.rset.discard(fid)
            self.wset.discard(fid)
            self.xset.discard(fid)

        def modify(self, fid, event):
            self.unregister(fid)
            self.register(fid, event)

        def poll(self, timeout):
            rlist, wlist, xlist = self.poller(self.rset, self.wset, self.xset, timeout)
            events = {}
            for fid in rlist:
                events[fid] = _AsyncPoller._Read
            for fid in wlist:
                events[fid] = events.get(fid, 0) | _AsyncPoller._Write
            for fid in xlist:
                events[fid] = events.get(fid, 0) | _AsyncPoller._Error

            return events.iteritems()

        def terminate(self):
            self.rset = set()
            self.wset = set()
            self.xset = set()

    AsynCoroSocket = _AsynCoroSocket
    _AsyncNotifier = _AsyncPoller

AsyncSocket = AsynCoroSocket

class Lock(object):
    """'Lock' primitive for coroutines.
    """
    def __init__(self):
        self._owner = None
        self._waitlist = []
        self._asyncoro = AsynCoro.instance()

    def acquire(self, blocking=True):
        """Must be used with 'yield' as 'yield lock.acquire()'.
        """
        if not blocking and self._owner is not None:
            raise StopIteration(False)
        coro = self._asyncoro.cur_coro()
        while self._owner is not None:
            self._waitlist.append(coro)
            yield coro._await_()
        self._owner = coro
        raise StopIteration(True)

    def release(self):
        """May be used with 'yield'.
        """
        coro = self._asyncoro.cur_coro()
        if self._owner is None:
            raise RuntimeError('"%s"/%s: invalid lock release - not locked' % (coro.name, coro._id))
        self._owner = None
        if self._waitlist:
            wake = self._waitlist.pop(0)
            wake._proceed_(True)

class RLock(object):
    """'RLock' primitive for coroutines.
    """
    def __init__(self):
        self._owner = None
        self._depth = 0
        self._waitlist = []
        self._asyncoro = AsynCoro.instance()

    def acquire(self, blocking=True):
        """Must be used with 'yield' as 'yield rlock.acquire()'.
        """
        coro = self._asyncoro.cur_coro()
        if self._owner == coro:
            assert self._depth > 0
            self._depth += 1
            raise StopIteration(True)
        if not blocking and self._owner is not None:
            raise StopIteration(False)
        while self._owner is not None:
            self._waitlist.append(coro)
            yield coro._await_()
        assert self._depth == 0
        self._owner = coro
        self._depth = 1
        raise StopIteration(True)

    def release(self):
        """May be used with 'yield'.
        """
        coro = self._asyncoro.cur_coro()
        if self._owner != coro:
            raise RuntimeError('"%s"/%s: invalid lock release - owned by "%s"/%s' % \
                               (coro.name, coro._id, self._owner.name, self._owner._id))
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            if self._waitlist:
                wake = self._waitlist.pop(0)
                wake._proceed_(True)

class Condition(object):
    """'Condition' primitive for coroutines.
    """
    def __init__(self):
        """TODO: support lock argument?
        """
        self._owner = None
        self._depth = 0
        self._waitlist = []
        self._notifylist = []
        self._asyncoro = AsynCoro.instance()

    def acquire(self, blocking=True):
        """Must be used with 'yield' as 'yield cv.acquire()'.
        """
        coro = self._asyncoro.cur_coro()
        if self._owner == coro:
            assert self._depth > 0
            self._depth += 1
            raise StopIteration(True)
        if not blocking and self._owner is not None:
            raise StopIteration(False)
        while self._owner is not None:
            self._waitlist.append(coro)
            yield coro._await_()
        assert self._depth == 0
        self._owner = coro
        self._depth = 1
        raise StopIteration(True)

    def release(self):
        """May be used with 'yield'.
        """
        coro = self._asyncoro.cur_coro()
        if self._owner != coro:
            raise RuntimeError('"%s"/%s: invalid lock release - owned by "%s"/%s' % \
                               (coro.name, coro._id, self._owner.name, self._owner._id))
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            if self._waitlist:
                wake = self._waitlist.pop(0)
                wake._proceed_(True)

    def notify(self, n=1):
        """May not be used with 'yield'.
        """
        while self._notifylist and n:
            wake = self._notifylist.pop(0)
            wake._proceed_(True)
            n -= 1

    def notify_all(self):
        self.notify(len(self._notifylist))

    notifyAll = notify_all

    def wait(self, timeout=None):
        """Must be used with 'yield' as 'yield cv.wait()'.
        """
        coro = self._asyncoro.cur_coro()
        if self._owner != coro:
            raise RuntimeError('"%s"/%s: invalid lock release - owned by "%s"/%s' % \
                               (coro.name, coro._id, self._owner.name, self._owner._id))
        assert self._depth > 0
        depth = self._depth
        self._depth = 0
        self._owner = None
        if self._waitlist:
            wake = self._waitlist.pop(0)
            wake._proceed_(True)
        self._notifylist.append(coro)
        start = _time()
        if (yield coro._await_(timeout)) is None:
            self._notifylist.remove(coro)
            raise StopIteration(False)
        while self._owner is not None:
            self._waitlist.insert(0, coro)
            if timeout is not None:
                timeout -= (_time() - start)
                if timeout <= 0:
                    raise StopIteration(False)
                start = _time()
            if (yield coro._await_(timeout)) is None:
                self._waitlist.remove(coro)
                raise StopIteration(False)
        assert self._depth == 0
        self._owner = coro
        self._depth = depth
        raise StopIteration(True)

class Event(object):
    """'Event' primitive for coroutines.
    """
    def __init__(self):
        self._flag = False
        self._waitlist = []
        self._asyncoro = AsynCoro.instance()

    def set(self):
        """May be used with 'yield'.
        """
        self._flag = True
        for coro in self._waitlist:
            coro._proceed_(True)
        self._waitlist = []

    def is_set(self):
        """No need to use with 'yield'.
        """
        return self._flag

    isSet = is_set

    def clear(self):
        """No need to use with 'yield'.
        """
        self._flag = False

    def wait(self, timeout=None):
        """Must be used with 'yield' as 'yield event.wait()' .
        """
        if self._flag:
            raise StopIteration(True)
        coro = self._asyncoro.cur_coro()
        if timeout is not None:
            if timeout <= 0:
                raise StopIteration(False)
        self._waitlist.append(coro)
        if (yield coro._await_(timeout)) is None:
            self._waitlist.remove(coro)
            raise StopIteration(False)
        else:
            raise StopIteration(True)

class Semaphore(object):
    """'Semaphore' primitive for coroutines.
    """
    def __init__(self, value=1):
        assert value >= 1
        self._waitlist = []
        self._counter = value
        self._asyncoro = AsynCoro.instance()

    def acquire(self, blocking=True):
        """Must be used with 'yield' as 'yield sem.acquire()'.
        """
        if blocking:
            coro = self._asyncoro.cur_coro()
            while self._counter == 0:
                self._waitlist.append(coro)
                yield coro._await_()
        elif self._counter == 0:
            raise StopIteration(False)
        self._counter -= 1
        raise StopIteration(True)

    def release(self):
        """May be used with 'yield'.
        """
        self._counter += 1
        assert self._counter > 0
        if self._waitlist:
            wake = self._waitlist.pop(0)
            wake._proceed_(True)

class HotSwapException(Exception):
    """This exception is used to indicate hot-swap request and
    response.

    If 'exc' is the exception, then len(exc.args) == 1 and
    exc.args[0] is the new generator method.
    """
    pass

class MonitorException(Exception):
    """This execption is used to indicate that a coroutine being
    monitored has finished or terminated.

    If 'exc' is the exception, then

    exc.args[0] is the coroutine (either local or remote) about
    which this exception is thrown,

    If the coroutine finished executing normally, then len(exc.args)
    == 2, type(exc.args[1]) == tuple, len(exc.args[1]) == 2,
    exc.args[1][0] is StopIteration and exc.args[1][1] is the value of
    coroutine (i.e., last value yielded). If the coroutine is remote
    and value (i.e., exc.args[1][1]) is not serializable, then
    exc.args[1][1] is just the type of that value.

    If the couroutine terminated due to exception, then exc.args[1:]
    (i.e., rest of exc.args) is the list of exceptions pending at the
    time coroutine terminated.
    """
    pass

class _NetRequest(object):
    """Internal use only.
    """

    __slots__ = ('name', 'kwargs', 'src', 'dst', 'auth', 'event', 'id', 'reply', 'timeout')

    def __init__(self, name, kwargs={}, src=None, dst=None, auth=None, timeout=None):
        self.name = name
        self.kwargs = kwargs
        self.src = src
        self.dst = dst
        self.auth = auth
        self.id = None
        self.event = None
        self.reply = None
        self.timeout = timeout

    def __getstate__(self):
        state = {'name':self.name, 'kwargs':self.kwargs, 'src':self.src, 'dst':self.dst,
                 'auth':self.auth, 'id':self.id, 'reply':self.reply, 'timeout':self.timeout}
        return state

    def __setstate__(self, state):
        for k, v in state.iteritems():
            setattr(self, k, v)

class _Peer(object):
    """Internal use only.
    """

    __slots__ = ('location', 'auth', 'keyfile', 'certfile', 'stream', 'conn',
                 'reqs', 'reqs_pending', 'req_coro')

    peers = {}

    def __init__(self, location, auth, keyfile, certfile):
        self.location = location
        self.auth = auth
        self.keyfile = keyfile
        self.certfile = certfile
        self.stream = False
        self.conn = None
        self.reqs = collections.deque()
        self.reqs_pending = Event()
        _Peer.peers[(location.addr, location.port)] = self
        self.req_coro = Coro(self.req_proc)

    @staticmethod
    def send_req(req):
        peer = _Peer.peers.get((req.dst.addr, req.dst.port), None)
        if peer is None:
            logger.debug('invalid peer: %s, %s' % (req.dst, req.name))
            return -1
        peer.reqs.append(req)
        peer.reqs_pending.set()
        return 0

    def req_proc(self, coro=None):
        coro.set_daemon()
        while 1:
            if not self.reqs:
                if not self.stream and self.conn:
                    self.conn.close()
                    self.conn = None
                self.reqs_pending.clear()
                yield self.reqs_pending.wait()
            req = self.reqs.popleft()
            if not self.conn:
                self.conn = AsyncSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                                        keyfile=self.keyfile, certfile=self.certfile)
                if req.timeout:
                    self.conn.settimeout(req.timeout)
                try:
                    yield self.conn.connect((self.location.addr, self.location.port))
                except:
                    # TODO: delete peer?
                    self.conn = None
                    req.reply = None
                    if req.event:
                        req.event.set()
                    continue
            else:
                self.conn.settimeout(req.timeout)

            req.auth = self.auth
            try:
                yield self.conn.send_msg(serialize(req))
                reply = yield self.conn.recv_msg()
                req.reply = unserialize(reply)
            except socket.error as exc:
                logger.debug('could not send "%s" to %s', req.name, self.location)
                # logger.debug(traceback.format_exc())
                if len(exc.args) == 1 and exc.args[0] == 'hangup':
                    logger.warning('peer "%s" not reachable' % self.location)
                    # TODO: remove peer?
                self.conn.close()
                self.conn = None
                req.reply = None
            except socket.timeout:
                self.conn.close()
                self.conn = None
                req.reply = None
            except:
                # logger.debug(traceback.format_exc())
                self.conn.close()
                self.conn = None
                req.reply = None
            finally:
                if req.event:
                    req.event.set()

    @staticmethod
    def remove(location):
        peer = _Peer.peers.pop((location.addr, location.port), None)
        if peer:
            peer.req_coro.terminate()

class Coro(object):
    """'Coroutine' factory to build coroutines to be scheduled with
    AsynCoro. Automatically starts executing 'target' generator
    function.  The function definition should have 'coro' keyword
    argument set to (default value) None. When the function is called,
    that argument will be set to Coro instance (object).
    """

    __slots__ = ('_generator', 'name', '_id', '_state', '_value', '_exceptions', '_callers',
                 '_timeout', '_daemon', '_complete', '_msgs', '_monitors', '_new_generator',
                 '_hot_swappable', '_location')

    _asyncoro = None

    def __init__(self, target, *args, **kwargs):
        self._generator = self.__get_generator(target, *args, **kwargs)
        self.name = target.__name__
        self._id = id(self)
        self._state = None
        self._value = None
        self._exceptions = []
        self._callers = []
        self._timeout = None
        self._daemon = False
        self._complete = None
        self._msgs = collections.deque()
        self._monitors = set()
        self._new_generator = None
        self._hot_swappable = False
        if Coro._asyncoro is None:
            Coro._asyncoro = AsynCoro.instance()
        self._location = Coro._asyncoro._location
        Coro._asyncoro._add(self)

    @staticmethod
    def locate(name, location=None, timeout=None):
        """Must be used with 'yield' as
        'rcoro = yield Coro.locate("name")'.

        Returns Coro instance to coroutine at remote peers so it can
        be used to exchange messages, monitor etc. (those methods
        explicitly marked as callable on remote coroutines).
        """
        if Coro._asyncoro is None:
            Coro._asyncoro = AsynCoro.instance()
        if location is None or location == Coro._asyncoro._location:
            rcoro = Coro._asyncoro._rcoros.get(name, None)
            if rcoro is not None or location == Coro._asyncoro._location:
                raise StopIteration(rcoro)
        if location is None:
            req = _NetRequest('locate_coro', kwargs={'name':name},
                              src=Coro._asyncoro._location, timeout=timeout)
            req.id = id(req)
            Coro._asyncoro._requests[req.id] = req
            req.event = Event()
            for (addr, port), peer in _Peer.peers.items():
                if req.event.is_set():
                    break
                yield Coro._asyncoro._async_reply(req, peer, dst=Location(addr, port))
            else:
                if (yield req.event.wait(timeout)) is False:
                    Coro._asyncoro._requests.pop(req.id, None)
                    req.reply = None
            rcoro = req.reply
        else:
            req = _NetRequest('locate_coro', kwargs={'name':name}, dst=location, timeout=timeout)
            rcoro = yield Coro._asyncoro._sync_reply(req)
        raise StopIteration(rcoro)

    def register(self, name=None):
        """Register this coroutine so coroutines running on a remote
        (peer) asyncoro can locate it (with 'locate') so they can
        exchange messages, monitored etc.
        """
        if self._location != Coro._asyncoro._location:
            return -1
        if not name:
            name = self.name
        return Coro._asyncoro._register_coro(self, name)

    def unregister(self, name=None):
        """Unregister this coroutine (see 'register' above).
        """
        if self._location != Coro._asyncoro._location:
            return -1
        if not name:
            name = self.name
        return Coro._asyncoro._unregister_coro(self, name)

    def location(self):
        """Get Location instance where this coro is running.

        Can also be used on remotely running coroutines.
        """
        return copy.copy(self._location)

    def set_daemon(self, flag=True):
        """Mark coroutine is a daemon process or not.

        AsynCoro scheduler waits for all non-daemon coroutines to
        terminate before it terminates itself.
        """
        if Coro._asyncoro.cur_coro() == self:
            Coro._asyncoro._set_daemon(self, bool(flag))
            return 0
        else:
            logger.warning('set_daemon must be called from running coro')
            return -1

    def suspend(self, timeout=None, alarm_value=None):
        """Must be used with 'yield' as 'yield coro.suspend()'.
        Suspend/sleep coro (until woken up, usually by AsyncNotifier
        in the case of AsynCoroSockets).

        If timeout is a (floating point) number, this coro is
        suspended for that many seconds (or fractions of second). This
        method should be used with 'yield' and from coro only.

        If suspend times out (no other coroutine resumes it), AsynCoro
        resumes it with the value 'alarm_value'.
        """
        return Coro._asyncoro._suspend(self, timeout, alarm_value, AsynCoro._Suspended)

    sleep = suspend

    def resume(self, update=None):
        """May be used with 'yield'. Resume/wakeup this coro and send
        'update' to it.

        The resuming coro gets 'update' for the 'yield' that caused it
        to suspend. If coro is currently not suspended/sleeping,
        resume is ignored.
        """
        return Coro._asyncoro._resume(self, update, AsynCoro._Suspended)

    wakeup = resume

    def send(self, message):
        """May be used with 'yield'. Sends 'message' to coro.

        If coro is currently waiting with 'receive', it is resumed
        with 'message'. Otherwise, 'message' is queued so that next
        receive call will return message.

        Can also be used on remotely running coroutines.
        """
        if self._location == Coro._asyncoro._location:
            return Coro._asyncoro._resume(self, message, AsynCoro._AwaitMsg_)
        else:
            request = _NetRequest('send', kwargs={'coro':self._id, 'message':message},
                                  dst=self._location, timeout=2)
            # request is queued for asynchronous processing
            if _Peer.send_req(request):
                logger.warning('remote coro at %s is not valid, unsubscribing it',
                               self._location)
                def _unsub(self, rchannels, coro=None):
                    for rchannel in rchannels:
                        yield rchannel.unsubscribe(self)
                Coro(_unsub, self, Coro._asyncoro._rchannels.values())
                return -1
            else:
                return 0

    def deliver(self, message, timeout=None):
        """Must be used with 'yield' as 'yield coro.deliver(message)'.

        Deliver message to coroutine to the 'real' coroutine to which
        this instance refers to and return number of coroutines that
        received the message.

        Can also be used on remotely running coroutines.
        """
        if self._location == Coro._asyncoro._location:
            reply = Coro._asyncoro._resume(self, message, AsynCoro._AwaitMsg_)
        else:
            request = _NetRequest('deliver', kwargs={'coro':self._id, 'message':message},
                                  dst=self._location, timeout=timeout)
            reply = yield Coro._asyncoro._sync_reply(request, alarm_value=0)
            if reply < 0:
                logger.warning('remote coro at %s is not valid, unsubscribing it', self._location)
                def _unsub(self, rchannels, coro=None):
                    for rchannel in rchannels:
                        yield rchannel.unsubscribe(self)
                Coro(_unsub, self, Coro._asyncoro._rchannels.values())
                reply = 0
        raise StopIteration(reply)

    def receive(self, timeout=None, alarm_value=None):
        """Must be used with 'yield' as 'message = yield coro.receive()'.
        Gets/waits for message.

        Gets earliest queued message if available (that has been sent
        earlier with 'send'). Otherwise, suspends until 'timeout'. If
        timeout happens, coro receives alarm_value.
        """
        return Coro._asyncoro._suspend(self, timeout, alarm_value, AsynCoro._AwaitMsg_)

    def throw(self, *args):
        """Throw exception in coroutine. This method must be called from
        coro only.
        """
        if len(args) == 0:
            logger.warning('throw: invalid argument(s)')
            return -1
        if len(args) == 1:
            if isinstance(args[0], tuple) and len(args[0]) > 1:
                args = args[0]
            else:
                args = (type(args[0]), args[0])
        return Coro._asyncoro._throw(self, *args)

    def value(self):
        """Get last value 'yield'ed by coro.

        NB: This method should _not_ be called from a coroutine! This
        method is meant for main thread in the user program to wait for
        (main) coroutine(s) it creates.

        Once coroutine stops (finishes) executing, the last value
        yielded by it is returned.
        """
        Coro._asyncoro._lock.acquire()
        if self._complete is None:
            self._complete = threading.Event()
            Coro._asyncoro._lock.release()
            self._complete.wait()
        elif self._complete == 0:
            Coro._asyncoro._lock.release()
        else:
            Coro._asyncoro._lock.release()
            self._complete.wait()
        return self._value

    def terminate(self):
        """Terminate coro.

        If this method called by a thread (and not a coro), there is a
        chance that coro being terminated is currently running and can
        interfere with GenratorExit exception that will be thrown to
        coro.
        """
        return Coro._asyncoro._terminate_coro(self)

    def hot_swappable(self, flag):
        if Coro._asyncoro.cur_coro() == self:
            if flag:
                self._hot_swappable = True
            else:
                self._hot_swappable = False
            return 0
        else:
            logger.warning('hot_swappable must be called from running coro')
            return -1

    def hot_swap(self, target, *args, **kwargs):
        """Replaces coro's generator function with given target(*args, **kwargs).

        The new generator starts executing from the beginning. If
        there are any pending messages, they will not be reset, so new
        generator can process them (or clear them with successive
        'receive' calls with timeout=0 until it returns
        'alarm_value').
        """
        try:
            generator = self.__get_generator(target, *args, **kwargs)
        except:
            logger.warning('%s is not a generator!' % target.__name__)
            return -1
        return Coro._asyncoro._swap_generator(self, generator)

    def monitor(self, observe):
        """Must be used with 'yield' as 'yield coro.monitor(observe)',
        where 'observe' is a coroutine which will be monitored by
        'coro'.  When 'observe' is finished (raises StopIteration or
        terminated due to uncaught exception), that exception is
        thrown to 'coro' (monitor coroutine).

        Monitor can inspect the exception and restart observed
        coroutine if necessary. 'observe' can be a remote coroutine.

        Can also be used on remotely running 'observe' coroutine.
        """
        if self._location == observe._location:
            ret = Coro._asyncoro._monitor(self, observe)
            raise StopIteration(ret)
        else:
            # remote coro
            request = _NetRequest('monitor', kwargs={'monitor':self, 'coro':observe},
                                  dst=observe._location, timeout=2)
            reply = yield Coro._asyncoro._sync_reply(request)
            raise StopIteration(reply)
        
    def restart(self, target, *args, **kwargs):
        """If this coroutine is monitored by another coroutine, that
        monitor can restart the coroutine with the (new) generator.

        Pending messages are not reset, so new generator can process
        them.
        """
        if self._generator:
            try:
                self._generator.close()
            except:
                logger.warning('closing %s raised exception: %s',
                               self._generator.__name__, traceback.format_exc())
        self._generator = self.__get_generator(target, *args, **kwargs)
        self.name = target.__name__
        self._value = None
        self._exceptions = []
        self._timeout = None
        self._daemon = False
        self._hot_swappable = False
        Coro._asyncoro._add(self)

    def _await_(self, timeout=None, alarm_value=None):
        """Internal use only.
        """
        return Coro._asyncoro._suspend(self, timeout, alarm_value, AsynCoro._AwaitIO_)

    def _proceed_(self, update=None):
        """Internal use only.
        """
        return Coro._asyncoro._resume(self, update, AsynCoro._AwaitIO_)

    def __get_generator(self, target, *args, **kwargs):
        if not inspect.isgeneratorfunction(target):
            raise Exception('%s is not a generator!' % target.__name__)
        if not args and kwargs:
            args = kwargs.pop('args', ())
            kwargs = kwargs.pop('kwargs', kwargs)
        if kwargs.get('coro', None) is not None:
            raise Exception('Coro function %s should not be called with ' \
                            '"coro" parameter' % target.__name__)
        # callargs = inspect.getcallargs(target, *args, **kwargs)
        # if 'coro' not in callargs or callargs['coro'] is not None:
        #     raise Exception('Coro function "%s" should have "coro" argument with ' \
        #                     'default value None' % target.__name__)
        kwargs['coro'] = self
        return target(*args, **kwargs)

    def __getstate__(self):
        state = {'name':self.name, '_id':str(self._id), '_location':self._location}
        return state
    
    def __setstate__(self, state):
        self.name = state['name']
        self._location = state['_location']
        self._id = state['_id']

    def __repr__(self):
        return '%s/%s@%s' % (self.name, self._id, self._location)

class Location(object):
    """Distributed asyncoro, coroutines, channels use Location to
    identify where they are running, where to send a message etc.

    Users can create instances (which can be passed to 'locate'
    methods) 'addr' is IP address and 'tcp_port' is TCP port (not UDP
    port) where (peer) asyncoro runs network services.
    """

    __slots__ = ('addr', 'port')

    def __init__(self, addr, tcp_port):
        self.addr = addr
        self.port = tcp_port

    def __eq__(self, other):
        return isinstance(other, type(self)) and \
               self.addr == other.addr and self.port == other.port

    def __ne__(self, other):
        return (not isinstance(other, type(self))) or \
               self.addr != other.addr or self.port != other.port

    def __repr__(self):
        return '%s:%s' % (self.addr, self.port)

class Channel(object):
    """Subscription based channel. Broadcasts a message to all
    registered subscribers, whether they are currently waiting for
    message or not. Messages are queued (buffered) at receiving
    coroutines. To get a message, a coro must use 'yield
    coro.receive()', with timeout and alarm_value, if necessary.

    Channels can be hierarchical, and subscribers can be remote.
    """

    __slots__ = ('name', '_location', '_transform', '_subscribers',
                 '_subscribe_event')

    _asyncoro = None

    def __init__(self, name, transform=None):
        """'name' must be unique across all channels.

        'transform' is a function that can either filter or
        transform a message. If the function returns 'None', the
        message is filtered (ignored). The function is called with
        first parameter set to channel name and second parameter set
        to the message.
        """

        if Channel._asyncoro is None:
            Channel._asyncoro = AsynCoro.instance()
        self.name = name
        self._location = Channel._asyncoro._location
        if transform is not None:
            try:
                argspec = inspect.getargspec(transform)
                assert len(argspec.args) == 2
            except:
                logger.warning('invalid "transform" function ignored')
                transform = None
        self._transform = transform
        self._subscribers = set()
        self._subscribe_event = Event()
        Channel._asyncoro._lock.acquire()
        if name in Channel._asyncoro._channels:
            Channel._asyncoro._lock.release()
            raise Exception('duplicate channel name "%s"' % name)
        else:
            Channel._asyncoro._channels[name] = self
            Channel._asyncoro._lock.release()

    @staticmethod
    def locate(name, location=None, timeout=None):
        """Must be used with 'yield' as
        'rchannel = yield Channel.locate("name")'.

        Returns Channel instance to registered channel at remote peers
        so it can be used to send/deliver messages..
        """
        if Channel._asyncoro is None:
            Channel._asyncoro = AsynCoro.instance()
        if location is None:
            req = _NetRequest('locate_channel', kwargs={'name':name},
                              src=Channel._asyncoro._location, timeout=None)
            req.event = Event()
            req.id = id(req)
            Channel._asyncoro._requests[req.id] = req
            for (addr, port), peer in _Peer.peers.items():
                if req.event.is_set():
                    break
                yield Channel._asyncoro._async_reply(req, peer, dst=Location(addr, port))
            else:
                if (yield req.event.wait(timeout)) is False:
                    Channel._asyncoro._requests.pop(req.id, None)
                    req.reply = None
            rchannel = req.reply
        else:
            req = _NetRequest('locate_channel', kwargs={'name':name}, dst=location, timeout=timeout)
            rchannel = yield Channel._asyncoro._sync_reply(req)
        raise StopIteration(rchannel)

    def register(self, name=None):
        """A registered channel can be located (with 'locate') by a
        coroutine on a remote asyncoro.
        """
        if self._location != Channel._asyncoro._location:
            return -1
        if not name:
            name = self.name
        return Channel._asyncoro._register_channel(self, name)

    def unregister(self, name=None):
        """Unregister channel (see 'register' above).
        """
        if self._location != Channel._asyncoro._location:
            return -1
        if not name:
            name = self.name
        return Channel._asyncoro._unregister_channel(self, name)

    def location(self):
        """Get Location instance where this channel is located.

        Can also be used on remote channels.
        """
        return copy.copy(self._location)

    def set_transform(self, transform):
        if self._location != Channel._asyncoro._location:
            return -1
        try:
            argspec = inspect.getargspec(transform)
            assert len(argspec.args) == 2
        except:
            logger.warning('invalid "transform" function ignored')
            return -1
        self._transform = transform
        return 0

    def subscribe(self, subscriber, timeout=None):
        """Must be used with 'yield', as, for example,
        'yield channel.subscribe(coro)'.

        Subscribe to receive messages. Senders don't need to
        subscribe. A message sent to this channel is delivered to all
        subscribers.

        Can also be used on remote channels.
        """
        if self._location == Channel._asyncoro._location:
            self._subscribers.add(subscriber)
            yield self._subscribe_event.set()
            reply = 0
        else:
            # remote channel
            kwargs = {'name':self.name}
            if isinstance(subscriber, Coro):
                kwargs['coro'] = subscriber
            elif isinstance(subscriber, Channel):
                kwargs['channel'] = subscriber
            else:
                raise Exception('invalid subscribe request')
            request = _NetRequest('subscribe', kwargs=kwargs, dst=self._location, timeout=timeout)
            reply = yield Channel._asyncoro._sync_reply(request)
        raise StopIteration(reply)

    def unsubscribe(self, subscriber, timeout=None):
        """Must be called with 'yield' as, for example,
        'yield channel.unsubscribe(coro)'.

        Future messages will not be delivered after unsubscribing.

        Can also be used on remote channels.
        """
        if self._location == Channel._asyncoro._location:
            if subscriber._location == self._location:
                self._subscribers.discard(subscriber)
            elif isinstance(subscriber, Coro):
                # remote coro
                for s in self._subscribers:
                    if isinstance(s, Coro) and \
                           s._id == subscriber._id and s._location == subscriber._location:
                        subscriber = s
                        break
                self._subscribers.discard(subscriber)
            elif isinstance(subscriber, Channel):
                # remote channel
                for s in self._subscribers:
                    if isinstance(s, Channel) and \
                           s.name == subscriber.name and s._location == subscriber._location:
                        subscriber = s
                        break
                self._subscribers.discard(subscriber)
            reply = 0
        else:
            # remote channel
            kwargs = {'name':self.name}
            if isinstance(subscriber, Coro):
                kwargs['coro'] = subscriber
            elif isinstance(subscriber, Channel):
                kwargs['channel'] = subscriber
            else:
                raise Exception('invalid unsubscribe request')
            request = _NetRequest('unsubscribe', kwargs=kwargs, dst=self._location, timeout=timeout)
            reply = yield Channel._asyncoro._sync_reply(request)
        raise StopIteration(reply)

    def send(self, message):
        """Message is sent to currently registered subscribers.

        Can also be used on remote channels.
        """
        if self._location == Channel._asyncoro._location:
            if self._transform:
                try:
                    message = self._transform(self.name, message)
                except:
                    message = None
                if message is None:
                    return -1
            for subscriber in self._subscribers:
                subscriber.send(message)
        else:
            # remote channel
            request = _NetRequest('send', kwargs={'name':self.name, 'message':message},
                                  dst=self._location, timeout=2)
            # request is queued for asynchronous processing
            if _Peer.send_req(request):
                logger.warning('remote channel at %s is not valid, unsubscribing it',
                               self._location)
                def _unsub(channel, rchannels, coro=None):
                    for rchannel in rchannels:
                        yield rchannel.unsubscribe(channel)
                Coro(_unsub, self, Channel._asyncoro._rchannels.values())
                return -1
        return 0

    def deliver(self, message, timeout=None, n=0):
        """Must be used with 'yield' as 'rcvd = yield channel.deliver(message)'.

        Blocking 'send': Wait until message can be delivered to at
        least 'n' subscribers before timeout. Returns number of
        end-point recipients (coroutines) the message is delivered to;
        i.e., in case of heirarchical channels, it is the sum of
        recipients of all the channels.

        Can also be used on remote channels.
        """
        if not isinstance(n, int) or n < 0:
            raise StopIteration(-1)
        if self._location == Channel._asyncoro._location:
            if self._transform:
                try:
                    message = self._transform(self.name, message)
                except:
                    message = None
                if message is None:
                    raise StopIteration(-1)
            if n:
                while len(self._subscribers) < n:
                    start = _time()
                    self._subscribe_event.clear()
                    if (yield self._subscribe_event.wait(timeout)) is False:
                        raise StopIteration(0)
                    if timeout is not None:
                        timeout -= _time() - start
                        if timeout <= 0:
                            raise StopIteration(0)
            # during delivery, _subscribers may change, so make copy
            subscribers = list(self._subscribers)
            count = {'reply':0, 'pending':len(subscribers), 'success':0}
            done = Event()

            def _deliver(subscriber, c, n, event, timeout, coro=None):
                try:
                    reply = yield subscriber.deliver(message, timeout=timeout)
                    if reply > 0:
                        c['reply'] += reply
                        c['success'] += 1
                        if n > 0 and c['success'] >= n:
                            event.set()
                except:
                    pass
                c['pending'] -= 1
                if c['pending'] == 0:
                    event.set()
            for subscriber in subscribers:
                if isinstance(subscriber, Coro) and self._location == subscriber._location:
                    if subscriber.send(message) == 0:
                        count['reply'] += 1
                        count['success'] += 1
                    count['pending'] -= 1
                else:
                    # channel/remote coro
                    Coro(_deliver, subscriber, count, n, done, timeout)
            if n == 0 or count['success'] < n:
                yield done.wait(timeout)
            raise StopIteration(count['reply'])
        else:
            # remote channel
            request = _NetRequest('deliver', kwargs={'name':self.name, 'message':message, 'n':n},
                                  dst=self._location, timeout=timeout)
            reply = yield Channel._asyncoro._sync_reply(request, alarm_value=0)
            if reply < 0:
                logger.warning('remote channel "%s" at %s may have gone away!',
                               self.name, self._location)
                # def _unsub(channel, rchannels, coro=None):
                #     for rchannel in rchannels:
                #         yield rchannel.unsubscribe(channel)
                # Coro(_unsub, self, Channel._asyncoro._rchannels.values())
                reply = 0
            raise StopIteration(reply)

    def __getstate__(self):
        state = {'name':self.name, '_location':self._location}
        return state

    def __setstate__(self, state):
        self.name = state['name']
        self._location = state['_location']
        self._transform = None

class RCI(object):
    """Remote Coro (Callable) Interface.

    Methods registered with RCI can be executed as coroutines on
    request (by remotely running coroutines).
    """

    __slots__ = ('name', '_location', '_method')

    _asyncoro = None

    def __init__(self, method, name=None):
        """'method' must be generator method; this is used to create
        coroutines. If 'name' is not given, method's function name is
        used for registering.
        """
        self._method = method
        if name:
            self.name = name
        elif inspect.isgeneratorfunction(method):
            self.name = method.__name__
        else:
            self.name = None
        if RCI._asyncoro is None:
            RCI._asyncoro = AsynCoro.instance()
        self._location = RCI._asyncoro._location

    @staticmethod
    def locate(name, location=None, timeout=None):
        """Must be used with 'yield' as
        'rci = yield RCI.locate("name")'.

        Returns RCI instance to registered RCI at a remote peer so
        its method can be used to execute coroutines at that peer.

        If 'location' is given, RCI is looked up at that specific
        peer; otherwise, all known peers are queried for given name.
        """
        if RCI._asyncoro is None:
            RCI._asyncoro = AsynCoro.instance()
        if location is None:
            req = _NetRequest('locate_rci', kwargs={'name':name},
                              src=RCI._asyncoro._location, timeout=None)
            req.event = Event()
            req.id = id(req)
            RCI._asyncoro._requests[req.id] = req
            for (addr, port), peer in _Peer.peers.items():
                if req.event.is_set():
                    break
                yield RCI._asyncoro._async_reply(req, peer, dst=Location(addr, port))
            else:
                if (yield req.event.wait(timeout)) is False:
                    RCI._asyncoro._requests.pop(req.id, None)
                    req.reply = None
            rci = req.reply
        else:
            req = _NetRequest('locate_rci', kwargs={'name':name}, dst=location, timeout=timeout)
            rci = yield RCI._asyncoro._sync_reply(req)
        raise StopIteration(rci)

    def register(self, name=None):
        """RCI must be registered so it can be located.
        """
        if self._location != RCI._asyncoro._location:
            return -1
        if not inspect.isgeneratorfunction(self._method):
            return -1
        RCI._asyncoro._lock.acquire()
        if not name:
            name = self.name
        else:
            self.name = name
        if RCI._asyncoro._rcis.get(name, None) is None:
            RCI._asyncoro._rcis[name] = self
            RCI._asyncoro._lock.release()
            return 0
        else:
            RCI._asyncoro._lock.release()
            return -1

    def unregister(self):
        """Unregister registered RCI; see 'register' above.
        """
        if self._location != RCI._asyncoro._location:
            return -1
        RCI._asyncoro._lock.acquire()
        if RCI._asyncoro._rcis.pop(self.name, None) is None:
            RCI._asyncoro._lock.release()
            return -1
        else:
            RCI._asyncoro._lock.release()
            return 0

    def __call__(self, *args, **kwargs):
        """Must be used with 'yeild' as 'val = yield rci(*args, **kwargs)'.

        Run RCI (method at remote location) with args and kwargs. Both
        args and kwargs must be serializable. Returns (remote) Coro
        instance.
        """
        req = _NetRequest('run_rci', kwargs={'name':self.name, 'args':args, 'kwargs':kwargs},
                          dst=self._location, timeout=2)
        reply = yield RCI._asyncoro._sync_reply(req)
        if isinstance(reply, Coro):
            raise StopIteration(reply)
        elif reply is None:
            raise StopIteration(None)
        else:
            raise Exception(reply)

    def __getstate__(self):
        state = {'name':self.name, '_location':self._location}
        return state

    def __setstate__(self, state):
        self.name = state['name']
        self._location = state['_location']

class AsynCoro(object):
    """Coroutine scheduler. Methods starting with '_' are for internal
    use only.

    AsynCoro can be initialized with an event notifier that provides
    'poll' and 'interrupt' methods. AsynCoro calls 'poll' method to
    deliver events to coroutines (typically, processing I/O events and
    calling 'resume' methods), and 'interrupt' method to cause current
    (blocking) 'poll' method to terminate so control is returned to
    AsynCoro. 'poll' method is called with timeout argument. If
    timeout is 0, 'poll' should deliver events without blocking, if
    timeout is None, 'poll' may block (i.e., can wait indefinitely for
    events to occur) and if timeout is a number, 'poll' should wait at
    most that many seconds before returning (control to AsynCoro).

    If either 'node' or 'udp_port' is not None, asyncoro runs network
    services so distributed coroutines can exhcnage messages. If
    'node' is not None, it must be either hostname or IP address where
    asyncoro runs network services. If 'udp_port' is not None, it is
    port number where asyncoro runs network services. If 'udp_port' is
    0, the default port number 51350 is used. If multiple instances of
    asyncoro are to be running on same host, they all can be started
    with the same 'udp_port', so that asyncoro instances automatically
    find each other.

    'name' is used in locating peers. They must be unique. If used in
    network mode and 'name' is not given, it is set to string
    'node:port'.

    'certfile' is path to file containing SSL certificate (see Python
    'ssl' module).

    'keyfile' is path to file containing private key for SSL
    communication (see Python 'ssl' module). This key may be stored in
    'certfile' itself, in which case this should be None.

    'ext_ip_addr' is the IP address of NAT firewall/gateway if
    asyncoro is behind that firewall/gateway.

    'dest_path_prefix' is path to directory (folder) where transferred
    files are saved. If path doesn't exist, asyncoro creates
    directory with that path. Senders may specify 'dest_path' with
    'send_file', in which case target file will be saved under
    dest_path_prefix + dest_path.

    'max_file_size' is maximum length of file in bytes allowed for
    transferred files. If it is 0 or None (default), there is no
    limit.
    """

    __metaclass__ = MetaSingleton
    __instance = None

    # in _scheduled set, waiting for turn to execute
    _Scheduled = 1
    # in _scheduled, currently executing
    _Running = 2
    # in _suspended, waiting for resume
    _Suspended = 3
    # in _suspended, waiting for I/O operation
    _AwaitIO_ = 4
    # in _suspended, waiting for message
    _AwaitMsg_ = 5

    def __init__(self, node=None, udp_port=None, tcp_port=0, ext_ip_addr=None,
                 name=None, secret='', certfile=None, keyfile=None, notifier=None,
                 dest_path_prefix=None, max_file_size=None):
        if self.__class__.__instance is None:
            self.__class__.__instance = self
            self._coros = {}
            self._cur_coro = None
            self._scheduled = set()
            self._suspended = set()
            self._timeouts = []
            # because Coro can be added from thread(s) and UDP poller in
            # the case of Windows (IOCP) runs in a separate thread, we
            # need to lock access to _scheduled, etc.
            self._lock = threading.RLock()
            self._terminate = False
            self._complete = threading.Event()
            self._daemons = 0
            if notifier is None:
                self._notifier = _AsyncNotifier()
            else:
                self._notifier = notifier
            self._polling = False
            self._tcp_sock = None
            self._udp_sock = None
            self._channels = {}
            self._scheduler = threading.Thread(target=self._schedule)
            self._scheduler.daemon = True
            self._scheduler.start()
            self._location = None
            self.name = name
            if udp_port is not None or node is not None:
                if node:
                    node = socket.gethostbyname(node)
                else:
                    node = socket.gethostbyname(socket.gethostname())
                self._stream_peers = {}
                self._rcoros = {}
                self._rchannels = {}
                self._rcis = {}
                self._requests = {}
                if not dest_path_prefix:
                    dest_path_prefix = os.path.join(os.sep, 'tmp', 'asyncoro')
                self.dest_path_prefix = os.path.abspath(dest_path_prefix)
                if not os.path.isdir(self.dest_path_prefix):
                    os.makedirs(self.dest_path_prefix)
                self.max_file_size = max_file_size
                if not udp_port:
                    udp_port = 51350
                self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except:
                    pass

                self._udp_sock.bind(('', udp_port))

                self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                if tcp_port:
                    try:
                        self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    except:
                        pass
                self._tcp_sock.bind((node, tcp_port))
                self._location = Location(*self._tcp_sock.getsockname())
                if not self._location.port:
                    raise Exception('could not start network server at %s' % (self._location))
                if ext_ip_addr:
                    try:
                        ext_ip_addr = socket.gethostbyname(ext_ip_addr)
                    except:
                        logger.warning('invalid ext_ip_addr ignored')
                    else:
                        self._location.addr = ext_ip_addr
                if not name:
                    self.name = str(self._location)

                self._secret = secret
                self._signature = os.urandom(20).encode('hex')
                self._auth_code = hashlib.sha1(self._signature + secret).hexdigest()
                self._certfile = certfile
                self._keyfile = keyfile
                self._tcp_sock.listen(32)
                logger.info('network server "%s" at %s, udp_port=%s, tcp_port=%s',
                            self.name, self._location.addr, self._udp_sock.getsockname()[1],
                            self._location.port)
                self._tcp_sock = AsynCoroSocket(self._tcp_sock, keyfile=self._keyfile,
                                                certfile=self._certfile)
                self._tcp_coro = Coro(self._tcp_proc)
                if self._udp_sock:
                    self._udp_sock = AsynCoroSocket(self._udp_sock)
                    self._udp_coro = Coro(self._udp_proc)
            atexit.register(self.terminate, True)

    @classmethod
    def instance(cls, *args, **kwargs):
        """Returns (singleton) instance of AsynCoro.
        """
        if cls.__instance is None:
            cls.__instance = cls(*args, **kwargs)
        return cls.__instance

    def cur_coro(self):
        """Must be called from a coro only.
        """
        return self._cur_coro

    def location(self):
        """Get Location instance where this asyncoro is running.
        """
        return copy.copy(self._location)

    def _add(self, coro):
        """Internal use only. See Coro class.
        """
        self._lock.acquire()
        self._coros[coro._id] = coro
        self._complete.clear()
        coro._state = AsynCoro._Scheduled
        self._scheduled.add(coro._id)
        if self._polling and len(self._scheduled) == 1:
            self._notifier.interrupt()
        self._lock.release()

    def _set_daemon(self, coro, flag):
        """Internal use only. See set_daemon in Coro.
        """
        self._lock.acquire()
        cid = coro._id
        coro = self._coros.get(cid, None)
        if coro is not None and hasattr(coro, '_daemon') and coro._daemon != flag:
            coro._daemon = flag
            if flag:
                self._daemons += 1
                if len(self._coros) == self._daemons:
                    self._complete.set()
            else:
                self._daemons -= 1
                self._complete.clear()
            self._lock.release()
            return 0
        else:
            self._lock.release()
            return -1

    def _monitor(self, monitor, coro):
        """Internal use only. See monitor in Coro.
        """
        self._lock.acquire()
        cid = coro._id
        coro = self._coros.get(cid, None)
        if coro is None:
            self._lock.release()
            logger.warning('monitor: invalid coroutine')
            return -1
        if monitor._location == self._location:
            mid = monitor._id
            monitor = self._coros.get(mid, None)
            if monitor is None or coro == monitor:
                self._lock.release()
                logger.warning('invalid monitor')
                return -1
            coro._monitors.add(monitor)
        elif isinstance(monitor, Coro):
            # remote monitor
            coro._monitors.add(monitor)
        else:
            self._lock.release()
            logger.warning('invalid monitor')
            return -1
        self._lock.release()
        return 0

    def _suspend(self, coro, timeout, alarm_value, state):
        """Internal use only. See sleep/suspend in Coro.
        """
        if timeout is not None:
            if not isinstance(timeout, (float, int)) or timeout < 0:
                logger.warning('invalid timeout %s', timeout)
                return -1
        assert state in (AsynCoro._AwaitIO_, AsynCoro._Suspended, AsynCoro._AwaitMsg_)
        self._lock.acquire()
        cid = coro._id
        coro = self._coros.get(cid, None)
        if coro is None or getattr(coro, '_state', None) != AsynCoro._Running:
            self._lock.release()
            logger.warning('invalid coroutine %s to suspend', cid)
            return -1
        if state == AsynCoro._AwaitMsg_ and coro._msgs:
            s, update = coro._msgs[0]
            if s == state:
                coro._msgs.popleft()
                self._lock.release()
                return update
        if timeout is None:
            coro._timeout = None
        elif timeout == 0:
            self._lock.release()
            return alarm_value
        else:
            timeout = _time() + timeout
            heappush(self._timeouts, (timeout, cid, alarm_value))
            coro._timeout = timeout
        self._scheduled.discard(cid)
        self._suspended.add(cid)
        coro._state = state
        self._lock.release()
        return 0

    def _resume(self, coro, update, state):
        """Internal use only. See resume in Coro.
        """
        self._lock.acquire()
        cid = coro._id
        coro = self._coros.get(cid, None)
        if coro is None or not hasattr(coro, '_state'):
            self._lock.release()
            logger.warning('invalid coroutine %s to resume', cid)
            return -1
        if state == AsynCoro._AwaitMsg_ and coro._state != state:
            coro._msgs.append((state, update))
            self._lock.release()
            return 0
        if coro._state == state:
            coro._timeout = None
            coro._value = update
            self._suspended.discard(cid)
            self._scheduled.add(cid)
            coro._state = AsynCoro._Scheduled
            if self._polling and len(self._scheduled) == 1:
                self._notifier.interrupt()
        else:
            logger.warning('ignoring resume for %s/%s', coro.name, cid)
        self._lock.release()
        return 0

    def _throw(self, coro, *args):
        """Internal use only. See throw in Coro.
        """
        self._lock.acquire()
        cid = coro._id
        coro = self._coros.get(cid, None)
        if coro is None or \
               getattr(coro, '_state', None) not in (AsynCoro._Scheduled, AsynCoro._Suspended,
                                                     AsynCoro._AwaitIO_, AsynCoro._AwaitMsg_):
            logger.warning('invalid coroutine %s to throw exception', cid)
            self._lock.release()
            return -1
        coro._timeout = None
        coro._exceptions.append(args)
        if coro._state in (AsynCoro._AwaitIO_, AsynCoro._Suspended, AsynCoro._AwaitMsg_):
            self._suspended.discard(cid)
            self._scheduled.add(cid)
            coro._state = AsynCoro._Scheduled
            if self._polling and len(self._scheduled) == 1:
                self._notifier.interrupt()
        self._lock.release()
        return 0

    def _terminate_coro(self, coro):
        """Internal use only.
        """
        self._lock.acquire()
        cid = coro._id
        coro = self._coros.get(cid, None)
        if coro is None or not hasattr(coro, '_state'):
            logger.warning('invalid coroutine %s to terminate', cid)
            self._lock.release()
            return -1
        # TODO: if currently waiting I/O or holding locks, warn?
        if coro._state in (AsynCoro._AwaitIO_, AsynCoro._Suspended, AsynCoro._AwaitMsg_):
            self._suspended.discard(cid)
            self._scheduled.add(cid)
        elif coro._state == AsynCoro._Running:
            logger.warning('coroutine to terminate %s/%s is running', coro.name, cid)
        coro._exceptions.append((GeneratorExit, GeneratorExit('close')))
        coro._timeout = None
        coro._state = AsynCoro._Scheduled
        coro._callers = []
        if self._polling and len(self._scheduled) == 1:
            self._notifier.interrupt()
        self._lock.release()
        return 0

    def _swap_generator(self, coro, generator):
        """Internal use only.
        """
        self._lock.acquire()
        cid = coro._id
        coro = self._coros.get(cid, None)
        if coro is None or not hasattr(coro, '_state'):
            logger.warning('invalid coroutine %s to terminate', cid)
            self._lock.release()
            return -1
        # TODO: prevent overwriting another generator already queued?
        if coro._callers or coro._state not in (AsynCoro._Scheduled, AsynCoro._Suspended) or \
               not coro._hot_swappable:
            logger.debug('postponing hot swapping of %s/%s', coro.name, cid)
            coro._new_generator = generator
            self._lock.release()
            return 1
        else:
            coro._timeout = None
            coro._exceptions.append((HotSwapException, HotSwapException(generator)))
            if coro._state == AsynCoro._Suspended:
                self._suspended.discard(cid)
                self._scheduled.add(cid)
                coro._state = AsynCoro._Scheduled
                if self._polling and len(self._scheduled) == 1:
                    self._notifier.interrupt()
        self._lock.release()
        return 0

    def _schedule(self):
        """Internal use only.
        """
        while not self._terminate:
            # process I/O events
            self._notifier.poll(0)
            self._lock.acquire()
            if not self._scheduled:
                if self._timeouts:
                    now = _time()
                    timeout = self._timeouts[0][0]
                    timeout -= now
                    if timeout < 0.001:
                        timeout = 0
                else:
                    timeout = None
                self._polling = True
                self._lock.release()
                self._notifier.poll(timeout)
                self._lock.acquire()
                self._polling = False
            if self._timeouts:
                # wake up timed suspends; pollers may timeout slightly
                # earlier, so give a bit of slack
                now = _time() + 0.001
                while self._timeouts and self._timeouts[0][0] <= now:
                    timeout, cid, alarm_value = heappop(self._timeouts)
                    assert timeout <= now
                    coro = self._coros.get(cid, None)
                    if coro is None or coro._timeout != timeout:
                        continue
                    if coro._state not in (AsynCoro._AwaitIO_, AsynCoro._Suspended,
                                           AsynCoro._AwaitMsg_):
                        logger.warning('coro %s/%s is in state %s for resume; ignored',
                                       coro.name, coro._id, coro._state)
                        continue
                    coro._timeout = None
                    self._suspended.discard(cid)
                    self._scheduled.add(cid)
                    coro._state = AsynCoro._Scheduled
                    coro._value = alarm_value
            scheduled = list(self._scheduled)
            # random.shuffle(scheduled)
            self._lock.release()

            for cid in scheduled:
                self._lock.acquire()
                coro = self._coros.get(cid, None)
                if coro is None or coro._state != AsynCoro._Scheduled:
                    self._lock.release()
                    if coro is not None:
                        logger.warning('ignoring %s/%s with state %s', coro.name, cid, coro._state)
                    continue
                coro._state = AsynCoro._Running
                self._cur_coro = coro
                self._lock.release()

                try:
                    if coro._exceptions:
                        exc = coro._exceptions.pop(0)
                        if exc[0] == GeneratorExit:
                            # assert str(exc[1]) == 'close'
                            coro._generator.close()
                        else:
                            retval = coro._generator.throw(*exc)
                    else:
                        retval = coro._generator.send(coro._value)
                except:
                    self._lock.acquire()
                    self._cur_coro = None
                    exc = sys.exc_info()
                    if exc[0] == StopIteration:
                        v = exc[1].args
                        if v:
                            if len(v) == 1:
                                coro._value = v[0]
                            else:
                                coro._value = v
                        coro._exceptions = []
                    elif exc[0] == HotSwapException:
                        v = exc[1].args
                        if isinstance(v, tuple) and len(v) == 1 and inspect.isgenerator(v[0]) and \
                               coro._hot_swappable and not coro._callers:
                            try:
                                coro._generator.close()
                            except:
                                logger.warning('closing %s/%s raised exception: %s',
                                               coro.name, cid, traceback.format_exc())
                            coro._generator = v[0]
                            coro.name = coro._generator.__name__
                            coro._exceptions = []
                            coro._value = None
                            # coro._msgs is not reset, so new
                            # generator can process pending messages
                            coro._state = AsynCoro._Scheduled
                        else:
                            logger.warning('invalid HotSwapException from %s/%s ignored',
                                           coro.name, cid)
                        self._lock.release()
                        continue
                    else:
                        coro._exceptions.append(exc)

                    if coro._callers:
                        # return to caller
                        caller = coro._callers.pop(-1)
                        coro._generator = caller[0]
                        if coro._exceptions:
                            # callee raised exception, restore saved value
                            coro._value = caller[1]
                            coro._state = AsynCoro._Scheduled
                        elif coro._state == AsynCoro._Running:
                            coro._state = AsynCoro._Scheduled
                    else:
                        if coro._exceptions:
                            exc = coro._exceptions[0]
                            assert isinstance(exc, tuple)
                            if len(exc) == 2:
                                exc = ''.join(traceback.format_exception_only(*exc))
                            else:
                                exc = ''.join(traceback.format_exception(*exc))
                            logger.warning('uncaught exception in %s:\n%s', coro.name, exc)
                            try:
                                coro._generator.close()
                            except:
                                logger.warning('closing %s raised exception: %s',
                                               coro.name, traceback.format_exc())
                        # delete this coro
                        if self._coros.pop(cid, None) == coro:
                            if coro._state not in (AsynCoro._Scheduled, AsynCoro._Running):
                                logger.warning('coro "%s" is in state: %s' % (coro.name, coro._state))
                            self._scheduled.discard(cid)
                            if coro._complete:
                                coro._complete.set()
                            else:
                                coro._complete = 0
                            coro._state = None
                            coro._generator = None
                            if coro._daemon is True:
                                self._daemons -= 1
                            if len(self._coros) == self._daemons:
                                self._complete.set()
                            monitors = list(coro._monitors)
                            for monitor in monitors:
                                if monitor._location == self._location:
                                    if coro._exceptions:
                                        exc = MonitorException(coro, coro._exceptions[0])
                                        coro._exceptions = []
                                    else:
                                        exc = MonitorException(coro, (StopIteration,
                                                                      StopIteration(coro._value)))
                                    monitor = self._coros.get(monitor._id, None)
                                    if monitor:
                                        monitor._timeout = None
                                        monitor._exceptions.append((MonitorException, exc))
                                        if monitor._state in (AsynCoro._AwaitIO_, AsynCoro._Suspended,
                                                              AsynCoro._AwaitMsg_):
                                            self._suspended.discard(monitor)
                                            self._scheduled.add(monitor)
                                            monitor._state = AsynCoro._Scheduled
                                    else:
                                        logger.warning('monitor for %s/%s has gone away!',
                                                       coro.name, coro._id)
                                else:
                                    # remote monitor
                                    # prepare serializable data to be sent over net
                                    if coro._exceptions:
                                        exc = coro._exceptions[0][:2]
                                        try:
                                            serialize(exc[1])
                                        except pickle.PicklingError:
                                            # send only the type
                                            exc = (exc[0], type(exc[1].args[0]))
                                        exc = MonitorException(coro, exc)
                                        coro._exceptions = []
                                    else:
                                        exc = coro._value
                                        try:
                                            serialize(exc)
                                        except pickle.PicklingError:
                                            exc = type(exc)
                                        exc = MonitorException(coro, (StopIteration,
                                                                      StopIteration(exc)))
                                    exc = (MonitorException, exc)
                                    request = _NetRequest('exception', {'exception':exc,
                                                                        'coro':monitor},
                                                          dst=monitor._location, timeout=2)
                                    _Peer.send_req(request)
                            if not coro._monitors or not coro._exceptions:
                                coro._msgs.clear()
                            coro._monitors.clear()
                        else:
                            logger.warning('coro %s/%s already removed?', coro.name, cid)
                    self._lock.release()
                else:
                    self._lock.acquire()
                    self._cur_coro = None
                    if coro._state == AsynCoro._Running:
                        coro._state = AsynCoro._Scheduled
                        # if this coroutine is suspended, don't update
                        # the value; when it is resumed, it will be
                        # updated with the 'update' value
                        coro._value = retval

                    if coro._new_generator is not None and not coro._callers and \
                           coro._hot_swappable and coro._state in [AsynCoro._Scheduled,
                                                                   AsynCoro._Suspended]:
                        coro._exceptions.append((HotSwapException,
                                                 HotSwapException(coro._new_generator)))
                        coro._new_generator = None
                    elif isinstance(retval, types.GeneratorType):
                        # push current generator onto stack and activate
                        # new generator
                        coro._callers.append((coro._generator, coro._value))
                        coro._generator = retval
                        coro._value = None
                    self._lock.release()

        self._lock.acquire()
        for cid, coro in self._coros.iteritems():
            logger.debug('terminating Coro %s/%s%s', coro.name, coro._id,
                         ' (daemon)' if coro._daemon else '')
            self._cur_coro = coro
            coro._state = AsynCoro._Running
            while coro._generator:
                try:
                    coro._generator.close()
                except:
                    logger.warning('closing %s raised exception: %s',
                                   coro._generator.__name__, traceback.format_exc())
                if coro._callers:
                    coro._generator, coro._value = coro._callers.pop(-1)
                else:
                    coro._generator = None
            if coro._complete:
                coro._complete.set()
            else:
                coro._complete = 0
        self._scheduled = set()
        self._suspended = set()
        self._timeouts = []
        self._coros = {}
        self._lock.release()
        self._complete.set()

    def terminate(self, await_non_daemons=False):
        """Terminate (singleton) instance of AsynCoro. This 'kills'
        all running coroutines.
        
        Should be called from main program (or a thread, but _not_
        from coroutines).
        """
        if not self._terminate:
            if await_non_daemons and len(self._coros) > self._daemons:
                logger.debug('waiting for %s coroutines to terminate',
                             len(self._coros) - self._daemons)
                self._complete.wait()
            if self._location:
                def _terminate(self, peer, location, coro=None):
                    req = _NetRequest('terminate', kwargs={'peer':self._location},
                                      dst=location, timeout=2)
                    yield self._sync_reply(req)
                for (addr, port), peer in _Peer.peers.items():
                    Coro(_terminate, self, peer, Location(addr, port))
                self._complete.wait()
            self._terminate = True
            if self._tcp_sock:
                self._tcp_sock.close()
            if self._udp_sock:
                self._udp_sock.close()
            self._notifier.interrupt()
            self._complete.wait()
            self._notifier.terminate()
            if hasattr(self, 'dest_path_prefix') and os.path.isdir(self.dest_path_prefix) and \
               len(os.listdir(self.dest_path_prefix)) == 0:
                os.rmdir(self.dest_path_prefix)
            logger.debug('AsynCoro terminated')

    def join(self, show_running=False):
        """Wait for currently scheduled coroutines to finish. AsynCoro
        continues to execute, so new coroutines can be added if
        necessary.
        """
        if show_running:
            self._lock.acquire()
            for coro in self._coros.itervalues():
                logger.info('waiting for %s/%s%s', coro.name, coro._id,
                            ' (daemon)' if coro._daemon else '')
            self._lock.release()
        self._complete.wait()

    def locate(self, name, timeout=None):
        """Must be used with 'yield' as
        'loc = yield scheduler.locate("peer")'.

        Find and return location of peer with 'name'.
        """
        req = _NetRequest('locate_peer', kwargs={'name':name}, src=self._location, timeout=None)
        req.event = Event()
        req.id = id(req)
        self._requests[req.id] = req
        for (addr, port), peer in _Peer.peers.items():
            if req.event.is_set():
                break
            yield self._async_reply(req, peer, dst=Location(addr, port))
        else:
            if (yield req.event.wait(timeout)) is False:
                self._requests.pop(req.id, None)
                req.reply = None
        loc = req.reply
        raise StopIteration(loc)

    def peer(self, node, udp_port=0, tcp_port=0, stream_send=False):
        """Must be used with 'yield', as
        'status = yield scheduler.peer("node1")'.

        Add asyncoro running at node, udp_port as peer to
        communicate. Peers on a local network can find each other
        automatically, but if they are on different networks, 'peer'
        can be used so they find each other.

        If 'tcp_port' is set, asyncoro will contact peer at given port
        (on 'node') using TCP, so UDP is not needed to discover node.

        If 'stream_send' is True, this asyncoro uses same connection
        again and again to send messages (i.e., as a stream) to peer
        'node' (instead of one message per connection). If 'tcp_port'
        is 0, then messages to all asyncoro instances on the given
        node will be streamed. If 'tcp_port' is a port number, then
        messages to only asyncoro running on that port will be
        streamed.
        """
        try:
            node = socket.gethostbyname(node)
        except:
            logger.warning('invalid node: "%s"', str(node))
            raise StopIteration(-1)
        if not udp_port:
            udp_port = 51350
        ping_msg = {'location':self._location, 'signature':self._signature, 'version':__version__}
        ping_msg = 'ping:' + serialize(ping_msg)
        stream_peers = [(addr, port, peer) for (addr, port), peer in _Peer.peers.iteritems() \
                        if (addr == node and (tcp_port == 0 or tcp_port == port))]
        if stream_send:
            for addr, port, peer in stream_peers:
                peer.stream = True
            self._stream_peers[(node, tcp_port)] = True
        else:
            for addr, port, peer in stream_peers:
                peer.stream = False
                self._stream_peers.pop((addr, port), None)
            self._stream_peers.pop((node, tcp_port), None)
        sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        sock.settimeout(2)
        try:
            yield sock.sendto(ping_msg, (node, udp_port))
        except:
            pass
        sock.close()

        if tcp_port:
            req = _NetRequest('ping', kwargs={'peer':self._location, 'signature':self._signature,
                                              'version':__version__}, dst=Location(node, tcp_port))
            sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            sock.settimeout(2)
            try:
                yield sock.connect((node, tcp_port))
                yield sock.send_msg(serialize(req))
            except:
                pass
            sock.close()

        raise StopIteration(0)

    def send_file(self, location, file, dest_path=None, overwrite=False, timeout=None):
        """Must be used with 'yield' as
        'loc = yield scheduler.send_file(location, "file1")'.

        Transfer 'file' to peer at 'location'. If 'dest_path' is not
        None, it must be a relative path (not absolute path), in which
        case, file will be saved at peer's dest_path_prefix +
        dest_path. Returns -1 in case of error, 0 if the file is
        transferred, 1 if the same file is already at the destination
        with same size, timestamp and permissions (so file is not
        transferred) and os.stat structure if a file with same name is
        at the destination with different size/timestamp/permissions,
        but 'overwrite' is False. If return value is 0, the sender may
        want to delete file with 'del_file' later.
        """
        try:
            stat_buf = os.stat(file)
        except:
            raise StopIteration(-1)
        if not ((stat.S_IMODE(stat_buf.st_mode) & stat.S_IREAD) and stat.S_ISREG(stat_buf.st_mode)):
            raise StopIteration(-1)
        if isinstance(dest_path, str) and dest_path:
            dest_path = dest_path.strip()
            # reject absolute path for dest_path
            if os.path.join(os.sep, dest_path) == dest_path:
                raise StopIteration(-1)
        peer = _Peer.peers.get((location.addr, location.port), None)
        if peer is None:
            logger.debug('%s is not a valid peer', location)
            raise StopIteration(-1)
        kwargs = {'file':os.path.basename(file), 'stat_buf':stat_buf,
                  'overwrite':overwrite == True, 'dest_path':dest_path}
        req = _NetRequest('send_file', kwargs=kwargs, dst=location, timeout=timeout)
        sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                              keyfile=self._keyfile, certfile=self._certfile)
        try:
            yield sock.connect((location.addr, location.port))
            req.auth = peer.auth
            yield sock.send_msg(serialize(req))
            reply = yield sock.recv_msg()
            reply = unserialize(reply)
            if reply == 0:
                fd = open(file, 'rb')
                while True:
                    data = fd.read(1024000)
                    if not data:
                        break
                    yield sock.sendall(data)
                fd.close()
                resp = yield sock.recv_msg()
                resp = unserialize(resp)
                if resp == 0:
                    reply = 0
                else:
                    reply = -1
        except socket.error as exc:
            reply = -1
            logger.debug('could not send "%s" to %s', req.name, location)
            if len(exc.args) == 1 and exc.args[0] == 'hangup':
                logger.warning('peer "%s" not reachable' % location)
                # TODO: remove peer?
        except socket.timeout:
            raise StopIteration(-1)
        except:
            reply = -1
        finally:
            sock.close()
        raise StopIteration(reply)

    def del_file(self, location, file, dest_path=None, timeout=None):
        """Must be used with 'yield' as
        'loc = yield scheduler.del_file(location, "file1")'.

        Delete 'file' from peer at 'location'. 'dest_path' must be
        same as that used for 'send_file'.
        """
        if isinstance(dest_path, str) and dest_path:
            dest_path = dest_path.strip()
            # reject absolute path for dest_path
            if os.path.join(os.sep, dest_path) == dest_path:
                raise StopIteration(-1)
        kwargs = {'file':os.path.basename(file), 'dest_path':dest_path}
        req = _NetRequest('del_file', kwargs=kwargs, dst=location, timeout=timeout)
        reply = yield self._sync_reply(req)
        if reply is None:
            reply = -1
        raise StopIteration(reply)

    def _tcp_proc(self, coro=None):
        """Internal use only.
        """
        coro.set_daemon()
        while True:
            conn, addr = yield self._tcp_sock.accept()
            Coro(self._tcp_task, conn, addr)

    def _udp_proc(self, coro=None):
        """Internal use only.
        """
        coro.set_daemon()
        ping_sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        ping_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        ping_sock.settimeout(2)
        ping_msg = {'location':self._location, 'signature':self._signature, 'version':__version__}
        ping_msg = 'ping:' + serialize(ping_msg)
        try:
            yield ping_sock.sendto(ping_msg, ('<broadcast>', self._udp_sock.getsockname()[1]))
        except:
            pass
        ping_sock.close()

        while True:
            msg, addr = yield self._udp_sock.recvfrom(1024)
            if not msg.startswith('ping:'):
                logger.warning('ignoring UDP message from %s:%s', addr[0], addr[1])
                continue
            try:
                info = unserialize(msg[len('ping:'):])
                assert info['version'] == __version__
                req_peer = info['location']
                auth_code = hashlib.sha1(info['signature'] + self._secret).hexdigest()
                if info['location'] == self._location:
                    continue
                peer = _Peer.peers.get((req_peer.addr, req_peer.port), None)
                if peer and peer.auth == auth_code:
                    continue
            except:
                continue

            req = _NetRequest('ping', kwargs={'peer':self._location, 'signature':self._signature,
                                              'version':__version__}, dst=req_peer, auth=auth_code)
            sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                                  keyfile=self._keyfile, certfile=self._certfile)
            sock.settimeout(2)
            try:
                yield sock.connect((req_peer.addr, req_peer.port))
                yield sock.send_msg(serialize(req))
            except:
                pass
            sock.close()

    def _tcp_task(self, conn, addr, coro=None):
        """Internal use only.
        """
        while True:
            msg = yield conn.recv_msg()
            if not msg:
                break
            req = None
            try:
                req = unserialize(msg)
                assert req.auth == self._auth_code
            except:
                if not req:
                    logger.debug('invalid message from %s:%s' % (addr[0], addr[1]))
                    break
                if req.name != 'ping':
                    logger.warning('invalid request %s from %s ignored: "%s", "%s"',
                                   req.name, req.src, req.auth, self._auth_code)
                    break

            if req.dst is not None and req.dst != self._location:
                logger.debug('invalid request "%s" to %s, %s (%s), %s',
                             req.name, req.src, req.dst, self._location, req.id)
                break

            if req.src == self._location:
                async_reply = req
                req = self._requests.pop(async_reply.id, None)
                if req is None:
                    logger.debug('ignoring request "%s"/%s', async_reply.name, async_reply.id)
                    break
                req.reply = async_reply.reply
                del async_reply
                req.event.set()
                break

            if req.name == 'send':
                # synchronous message
                assert req.src is None
                reply = -1
                if req.dst != self._location:
                    logger.warning('ignoring invalid "send" (%s != %s)' % (req.dst, self._location))
                else:
                    cid = req.kwargs.get('coro', None)
                    if cid is not None:
                        coro = self._coros.get(int(cid), None)
                        if coro is not None:
                            reply = coro.send(req.kwargs['message'])
                        else:
                            logger.warning('ignoring message to invalid coro %s', cid)
                    else:
                        name = req.kwargs.get('name', None)
                        if name is not None:
                            channel = self._channels.get(name, None)
                            if channel is not None:
                                reply = channel.send(req.kwargs['message'])
                            else:
                                logger.warning('ignoring message to channel "%s"', name)
                        else:
                            logger.warning('ignoring invalid recipient to "send"')
                yield conn.send_msg(serialize(reply))
            elif req.name == 'deliver':
                # synchronous message
                assert req.src is None
                reply = -1
                if req.dst != self._location:
                    logger.warning('ignoring invalid "deliver" (%s != %s)' % (req.dst, self._location))
                else:
                    cid = req.kwargs.get('coro', None)
                    if cid is not None:
                        coro = self._coros.get(int(cid), None)
                        if coro is not None:
                            coro.send(req.kwargs['message'])
                            reply = 1
                        else:
                            logger.warning('ignoring message to invalid coro %s', cid)
                    else:
                        name = req.kwargs.get('name', None)
                        if name is not None:
                            channel = self._channels.get(name, None)
                            if channel is not None:
                                reply = yield channel.deliver(
                                    req.kwargs['message'], timeout=req.timeout, n=req.kwargs['n'])
                            else:
                                logger.warning('ignoring message to channel "%s"', name)
                        else:
                            logger.warning('ignoring invalid recipient to "send"')
                yield conn.send_msg(serialize(reply))
            elif req.name == 'run_rci':
                # synchronous message
                assert req.src is None
                if req.dst != self._location:
                    reply = Exception('invalid RCI invocation')
                else:
                    rci = self._rcis.get(req.kwargs['name'], None)
                    if rci is None:
                        reply = Exception('RCI "%s" is not registered' % req.kwargs['name'])
                    else:
                        args = req.kwargs['args']
                        kwargs = req.kwargs['kwargs']
                        try:
                            reply = Coro(rci._method, *args, **kwargs)
                        except:
                            reply = Exception(traceback.format_exc())
                yield conn.send_msg(serialize(reply))
            elif req.name == 'locate_channel':
                channel = self._rchannels.get(req.kwargs['name'], None)
                if channel is not None or req.dst == self._location:
                    if req.src:
                        peer = _Peer.peers.get((req.src.addr, req.src.port), None)
                        if peer:
                            sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                                                  keyfile=self._keyfile, certfile=self._certfile)
                            try:
                                yield sock.connect((req.src.addr, req.src.port))
                                req.auth = peer.auth
                                req.reply = channel
                                yield sock.send_msg(serialize(req))
                            except:
                                pass
                            sock.close()
                    else:
                        yield conn.send_msg(serialize(channel))
            elif req.name == 'locate_coro':
                coro = self._rcoros.get(req.kwargs['name'], None)
                if coro is not None or req.dst == self._location:
                    if req.src:
                        peer = _Peer.peers.get((req.src.addr, req.src.port), None)
                        if peer:
                            sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                                                  keyfile=self._keyfile, certfile=self._certfile)
                            try:
                                yield sock.connect((req.src.addr, req.src.port))
                                req.auth = peer.auth
                                req.reply = coro
                                yield sock.send_msg(serialize(req))
                            except:
                                pass
                            sock.close()
                    else:
                        yield conn.send_msg(serialize(coro))
            elif req.name == 'locate_rci':
                rci = self._rcis.get(req.kwargs['name'], None)
                if rci is not None or req.dst == self._location:
                    if req.src:
                        peer = _Peer.peers.get((req.src.addr, req.src.port), None)
                        if peer:
                            sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                                                  keyfile=self._keyfile, certfile=self._certfile)
                            try:
                                yield sock.connect((req.src.addr, req.src.port))
                                req.auth = peer.auth
                                req.reply = rci
                                yield sock.send_msg(serialize(req))
                            except:
                                pass
                            sock.close()
                    else:
                        yield conn.send_msg(serialize(rci))
            elif req.name == 'subscribe':
                # synchronous message
                assert req.src is None
                assert req.dst == self._location
                reply = -1
                channel = self._rchannels.get(req.kwargs['name'], None)
                if channel is not None and channel._location == self._location:
                    subscriber = None
                    coro = req.kwargs.get('coro', None)
                    if coro is not None:
                        subscriber = coro
                    else:
                        rchannel = req.kwargs.get('channel', None)
                        if rchannel is not None:
                            subscriber = rchannel
                    if subscriber is not None:
                        reply = yield channel.subscribe(subscriber)
                yield conn.send_msg(serialize(reply))
            elif req.name == 'monitor':
                # synchronous message
                assert req.src is None
                assert req.dst == self._location
                reply = -1
                rcoro = req.kwargs.get('coro', None)
                monitor = req.kwargs.get('monitor', None)
                if isinstance(rcoro, Coro) and isinstance(monitor, Coro):
                    coro = self._coros.get(int(rcoro._id), None)
                    if isinstance(coro, Coro):
                        assert monitor._location != self._location
                        reply = self._monitor(monitor, coro)
                yield conn.send_msg(serialize(reply))
            elif req.name == 'exception':
                # synchronous message
                assert req.src is None
                assert req.dst == self._location
                reply = -1
                rcoro = req.kwargs.get('coro', None)
                if isinstance(rcoro, Coro):
                    coro = self._coros.get(int(rcoro._id), None)
                    if isinstance(coro, Coro):
                        exc = req.kwargs.get('exception', None)
                        if isinstance(exc, tuple):
                            reply = self._throw(coro, *exc)
                yield conn.send_msg(serialize(reply))
            elif req.name == 'ping':
                try:
                    req_peer = req.kwargs['peer']
                    auth_code = hashlib.sha1(req.kwargs['signature'] + self._secret).hexdigest()
                    assert req.kwargs['version'] == __version__
                except:
                    # logger.debug(traceback.format_exc())
                    break
                if req_peer == self._location:
                    break
                peer = _Peer.peers.get((req_peer.addr, req_peer.port), None)
                if peer and peer.auth == auth_code:
                    logger.debug('ignoring peer: %s' % (req_peer))
                    break
                pong = _NetRequest('pong',
                                   kwargs={'peer':self._location, 'signature':self._signature,
                                           'version':__version__}, dst=req_peer, auth=auth_code)
                sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                                      keyfile=self._keyfile, certfile=self._certfile)
                sock.settimeout(2)
                try:
                    yield sock.connect((req_peer.addr, req_peer.port))
                    yield sock.send_msg(serialize(pong))
                    reply = yield sock.recv_msg()
                    assert reply == 'ack'
                except:
                    logger.debug('ignoring peer %s', req_peer)
                    break
                finally:
                    sock.close()

                logger.debug('found asyncoro at %s' % req_peer)
                # relay ping to other asyncoro's running on same node
                peers = [(port, peer) for ((addr, port), peer) in _Peer.peers.items() \
                         if addr == self._location.addr and port != self._location.port]
                for port, peer in peers:
                    relay_req = _NetRequest('ping',
                                            kwargs={'peer':req_peer, 'version':__version__,
                                                    'signature':req.kwargs['signature']},
                                            dst=Location(self._location.addr, port), timeout=1)
                    _Peer.send_req(relay_req)

                if (req_peer.addr, req_peer.port) in _Peer.peers:
                    break
                peer = _Peer(req_peer, auth_code, self._keyfile, self._certfile)
                if (req_peer.addr, req_peer.port) in self._stream_peers or \
                       (req_peer.addr, 0) in self._stream_peers:
                    peer.stream = True

                # send pending (async) requests
                pending_reqs = [(i, req) for i, req in self._requests.iteritems() \
                                if req.dst is None or req.dst == req_peer]
                for rid, pending_req in pending_reqs:
                    sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                                          keyfile=self._keyfile, certfile=self._certfile)
                    if pending_req.timeout:
                        sock.settimeout(pending_req.timeout)
                    try:
                        yield sock.connect((req_peer.addr, req_peer.port))
                        pending_req.auth = auth_code
                        yield sock.send_msg(serialize(pending_req))
                    except:
                        # logger.debug(traceback.format_exc())
                        pass
                    sock.close()
            elif req.name == 'pong':
                try:
                    req_peer = req.kwargs['peer']
                    assert req.kwargs['version'] == __version__
                    auth_code = hashlib.sha1(req.kwargs['signature'] + self._secret).hexdigest()
                    # assert req_peer == req.src
                    peer = _Peer.peers.get((req_peer.addr, req_peer.port), None)
                    if peer and peer.auth == auth_code:
                        logger.debug('ignoring peer: %s' % (req_peer))
                        yield conn.send_msg('nak')
                        break
                    yield conn.send_msg('ack')
                except:
                    logger.debug('ignoring peer: %s' % req_peer)
                    # logger.debug(traceback.format_exc())
                    break

                logger.debug('found asyncoro at %s' % req_peer)
                # relay ping to other asyncoro's running on same node
                peers = [(port, peer) for ((addr, port), peer) in _Peer.peers.items() \
                         if addr == self._location.addr and port != self._location.port]
                for port, peer in peers:
                    relay_req = _NetRequest('ping',
                                            kwargs={'peer':req_peer, 'version':__version__,
                                                    'signature':req.kwargs['signature']},
                                            dst=Location(self._location.addr, port), timeout=1)
                    _Peer.send_req(relay_req)

                if (req_peer.addr, req_peer.port) in _Peer.peers:
                    break
                peer = _Peer(req_peer, auth_code, self._keyfile, self._certfile)
                if (req_peer.addr, req_peer.port) in self._stream_peers or \
                       (req_peer.addr, 0) in self._stream_peers:
                    peer.stream = True

                # send pending (async) requests
                pending_reqs = [(i, req) for i, req in self._requests.iteritems() \
                                if req.dst is None or req.dst == req_peer]
                for rid, pending_req in pending_reqs:
                    sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                                          keyfile=self._keyfile, certfile=self._certfile)
                    if pending_req.timeout:
                        sock.settimeout(pending_req.timeout)
                    try:
                        yield sock.connect((req_peer.addr, req_peer.port))
                        pending_req.auth = auth_code
                        yield sock.send_msg(serialize(pending_req))
                    except:
                        # logger.debug(traceback.format_exc())
                        pass
                    sock.close()
            elif req.name == 'unsubscribe':
                # synchronous message
                assert req.src is None
                assert req.dst == self._location
                reply = -1
                channel = self._rchannels.get(req.kwargs['name'], None)
                if channel is not None and channel._location == self._location:
                    rcoro = req.kwargs.get('coro', None)
                    if rcoro is not None:
                        subscriber = rcoro
                    else:
                        rchannel = req.kwargs.get('channel', None)
                        if rchannel is not None:
                            subscriber = rchannel
                    if subscriber is not None:
                        reply = yield channel.unsubscribe(subscriber)
                yield conn.send_msg(serialize(reply))
            elif req.name == 'locate_peer':
                if req.kwargs['name'] == self.name or req.dst == self._location:
                    if req.kwargs['name'] == self.name:
                        loc = self._location
                    elif req.dst == self._location:
                        loc = None
                    if req.src:
                        peer = _Peer.peers.get((req.src.addr, req.src.port), None)
                        if peer:
                            sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                                                  keyfile=self._keyfile, certfile=self._certfile)
                            try:
                                yield sock.connect((req.src.addr, req.src.port))
                                req.auth = peer.auth
                                req.reply = loc
                                yield sock.send_msg(serialize(req))
                            except:
                                pass
                            sock.close()
                    else:
                        yield conn.send_msg(serialize(loc))
            elif req.name == 'send_file':
                # synchronous message
                assert req.src is None
                assert req.dst == self._location
                tgt = os.path.basename(req.kwargs['file'])
                dest_path = req.kwargs['dest_path']
                if isinstance(dest_path, str):
                    tgt = os.path.join(dest_path, tgt)
                tgt = os.path.abspath(os.path.join(self.dest_path_prefix, tgt))
                stat_buf = req.kwargs['stat_buf']
                resp = 0
                if self.max_file_size and stat_buf.st_size > self.max_file_size:
                    logger.warning('file "%s" too big (%s) - must be smaller than %s',
                                   req.kwargs['file'], stat_buf.st_size, self.max_file_size)
                    resp = -1
                elif not tgt.startswith(self.dest_path_prefix):
                    resp = -1
                elif os.path.isfile(tgt):
                    sbuf = os.stat(tgt)
                    if abs(stat_buf.st_mtime - sbuf.st_mtime) <= 1 and \
                           stat_buf.st_size == sbuf.st_size and \
                           stat.S_IMODE(stat_buf.st_mode) == stat.S_IMODE(sbuf.st_mode):
                        resp = 1
                    elif not req.kwargs['overwrite']:
                        resp = sbuf

                if resp == 0:
                    try:
                        if not os.path.isdir(os.path.dirname(tgt)):
                            os.makedirs(os.path.dirname(tgt))
                        fd = open(tgt, 'wb')
                    except:
                        logger.debug('failed to create "%s" : %s', tgt, traceback.format_exc())
                        resp = -1
                yield conn.send_msg(serialize(resp))
                if resp == 0:
                    n = 0
                    try:
                        while n < stat_buf.st_size:
                            data = yield conn.recvall(min(stat_buf.st_size-n, 10240000))
                            if not data:
                                break
                            fd.write(data)
                            n += len(data)
                    except:
                        logger.warning('copying file "%s" failed', tgt)
                    fd.close()
                    if n < stat_buf.st_size:
                        os.remove(tgt)
                        resp = -1
                    else:
                        resp = 0
                        logger.debug('saved file %s', tgt)
                        os.utime(tgt, (stat_buf.st_atime, stat_buf.st_mtime))
                        os.chmod(tgt, stat.S_IMODE(stat_buf.st_mode))
                    yield conn.send_msg(serialize(resp))
            elif req.name == 'del_file':
                # synchronous message
                assert req.src is None
                assert req.dst == self._location
                tgt = os.path.basename(req.kwargs['file'])
                dest_path = req.kwargs['dest_path']
                if isinstance(dest_path, str) and dest_path:
                    tgt = os.path.join(dest_path, tgt)
                tgt = os.path.join(self.dest_path_prefix, tgt)
                if tgt.startswith(self.dest_path_prefix) and os.path.isfile(tgt):
                    os.remove(tgt)
                    d = os.path.dirname(tgt)
                    try:
                        while d > self.dest_path_prefix and os.path.isdir(d):
                            os.rmdir(d)
                            d = os.path.dirname(d)
                    except:
                        # logger.debug(traceback.format_exc())
                        pass
                    reply = 0
                else:
                    reply = -1
                yield conn.send_msg(serialize(reply))
            elif req.name == 'terminate':
                # synchronous message
                assert req.src is None
                peer = req.kwargs.get('peer', None)
                if peer:
                    logger.debug('peer %s:%s terminated' % (peer.addr, peer.port))
                    _Peer.remove(peer)
                try:
                    yield conn.send_msg(serialize('ack'))
                except:
                    pass
                break
            else:
                logger.warning('invalid request "%s" ignored', req.name)
        conn.close()

    def _async_reply(self, req, peer, dst=None):
        """Internal use only.
        """
        if dst is None:
            dst = req.dst
        sock = AsynCoroSocket(socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                              keyfile=self._keyfile, certfile=self._certfile)
        if req.timeout:
            sock.settimeout(req.timeout)
        try:
            yield sock.connect((dst.addr, dst.port))
            req.auth = peer.auth
            yield sock.send_msg(serialize(req))
        except socket.error as exc:
            logger.debug('could not send "%s" to %s', req.name, dst)
            if len(exc.args) == 1 and exc.args[0] == 'hangup':
                logger.warning('peer "%s" not reachable' % dst)
                # TODO: remove peer?
        except:
            logger.debug('could not send "%s" to %s', req.name, dst)
        sock.close()

    def _sync_reply(self, req, alarm_value=None):
        """Internal use only.
        """
        assert req.src is None
        req.event = Event()
        _Peer.send_req(req)
        if (yield req.event.wait(req.timeout)) is False:
            raise StopIteration(alarm_value)
        raise StopIteration(req.reply)

    def _register_channel(self, channel, name):
        """Internal use only.
        """
        if self._rchannels.get(name, None) is None:
            self._rchannels[name] = channel
            return 0
        else:
            logger.warning('channel "%s" is already registered', name)
            return -1

    def _unregister_channel(self, channel, name):
        """Internal use only.
        """
        if self._rchannels.get(name, None) is channel:
            self._rchannels.pop(name)
            return 0
        else:
            logger.warning('unregister of "%s" is invalid', name)
            return -1

    def _register_coro(self, coro, name):
        """Internal use only.
        """
        if self._rcoros.get(name, None) is None:
            self._rcoros[name] = coro
            return 0
        else:
            logger.warning('coro "%s" is already registered', name)
            return -1

    def _unregister_coro(self, coro, name):
        """Internal use only.
        """
        if self._rcoros.get(name, None) is coro:
            self._rcoros.pop(name)
            return 0
        else:
            logger.warning('unregister of "%s" is invalid', name)
            return -1

class AsynCoroThreadPool(object):
    """Schedule synchronous tasks with threads to be executed
    asynchronously.

    NB: As coroutines run in a separate thread, any variables shared
    between coroutines and tasks scheduled with thread pool must be
    protected by thread locking (not coroutine locking).
    """
    def __init__(self, num_threads):
        self._num_threads = num_threads
        self._task_queue = Queue.Queue()
        for n in xrange(num_threads):
            tasklet = threading.Thread(target=self._tasklet)
            tasklet.daemon = True
            tasklet.start()

    def _tasklet(self):
        while True:
            item = self._task_queue.get(block=True)
            if item is None:
                self._task_queue.task_done()
                break
            coro, func, args, kwargs = item
            try:
                coro._proceed_(func(*args, **kwargs))
            except:
                coro.throw(*sys.exc_info())
            finally:
                self._task_queue.task_done()

    def async_task(self, coro, target, *args, **kwargs):
        """Must be used with 'yield', as
        'yield pool.async_task(coro, generator)'.

        @coro is coroutine where this method is called. 

        @target is function/method that will be executed
          asynchronously in a thread.

        @args and @kwargs are arguments and keyword arguments passed
          to @target.

        This call effectively returns whatever target(*args, **kwargs) returns.
        """

        if not inspect.isroutine(target):
            raise RuntimeError('invalid usage: "target" must be function or method')
        # if arguments are passed as per Thread call, get args and kwargs
        if not args and kwargs:
            args = kwargs.pop('args', ())
            kwargs = kwargs.pop('kwargs', kwargs)
        coro._await_()
        self._task_queue.put((coro, target, args, kwargs))

    def join(self):
        """Wait till all scheduled tasks are completed.
        """
        self._task_queue.join()

    def terminate(self):
        """Wait for all scheduled tasks to complete and terminate
        threads.
        """
        for n in xrange(self._num_threads):
            self._task_queue.put(None)
        self._task_queue.join()

class AsynCoroDBCursor(object):
    """Database cursor proxy for asynchronous processing of executions.

    Since connections (and cursors) can't be shared in threads,
    operations on same cursor are run sequentially.
    """

    def __init__(self, thread_pool, cursor):
        self._thread_pool = thread_pool
        self._cursor = cursor
        self._sem = Semaphore()
        self._asyncoro = AsynCoro.instance()

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def _exec_task(self, func):
        try:
            return func()
        finally:
            self._sem.release()

    def execute(self, query, args=None):
        """Must be used with 'yield' as 'n = yield cursor.execute(stmt)'.
        """
        yield self._sem.acquire()
        coro = self._asyncoro.cur_coro()
        self._thread_pool.async_task(coro, self._exec_task,
                                     functools.partial(self._cursor.execute, query, args))

    def executemany(self, query, args):
        """Must be used with 'yield' as 'n = yield cursor.executemany(stmt)'.
        """
        yield self._sem.acquire()
        coro = self._asyncoro.cur_coro()
        self._thread_pool.async_task(coro, self._exec_task,
                                     functools.partial(self._cursor.executemany, query, args))

    def callproc(self, proc, args=()):
        """Must be used with 'yield' as 'yield cursor.callproc(proc)'.
        """
        yield self._sem.acquire()
        coro = self._asyncoro.cur_coro()
        self._thread_pool.async_task(coro, self._exec_task,
                                     functools.partial(self._cursor.callproc, proc, args))

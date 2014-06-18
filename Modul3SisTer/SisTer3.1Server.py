'''
Created on May 26, 2014

@author: Wildhan Ibrahim
'''
from ladon.ladonizer import ladonize
import hashlib

class Hashing(object):

        @ladonize(str,rtype=str)
        def MD5(self,a) :
                return hashlib.md5(a).hexdigest()

        @ladonize(str,rtype=str)
        def SHA1(self,a) :
                return hashlib.sha1(a).hexdigest()

        @ladonize(str,rtype=str)
        def SHA224(self,a) :
                return hashlib.sha224(a).hexdigest()

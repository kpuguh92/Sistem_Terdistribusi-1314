#!/usr/bin/env python

import sys															
from omniORB import CORBA											
import _GlobalIDL

loc = ["ORBInitialHost", "localhost", "ORBInitialPort" "1050"] 
orb = CORBA.ORB_init(loc, CORBA.ORB_ID)

ior = "corbaloc::localhost:1050/PingService"
obj = orb.string_to_object(ior) 

eo = obj._narrow(_GlobalIDL.Service)

mesg ="Hello World from Python"
result = eo.hello(mesg) 

print "Hasil dari Server %s." % result

from suds.client import Client
import math

##setting proxy utk terhubung via server proksi
#proxy = urllib2.ProxyHandler({"http":"http://karsono.puguh11@mhs.if.its.ac.id:C418_RailF4nZ@proxy.its.ac.id:8080"}) 
#opener = urllib2.build_opener(proxy, urllib2.HTTPHandler)
#urllib2.install_opener(opener)

##alamat web service
client = Client('http://www.webservicex.net/CurrencyConvertor.asmx?wsdl')
#print client

##input konversi mata uang
from_curr = raw_input('Masukkan kode mata uang yang ingin dikonversi: ')
to_curr = raw_input('Masukkan kode mata uang hasil konversi: ')

##menghitung hasil konversi mata uang
curr = client.service.ConversionRate(from_curr, to_curr)
print "Hasil konversi: 1", from_curr, "=", curr, to_curr, "."
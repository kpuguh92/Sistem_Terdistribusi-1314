from suds.client import Client
import xml.etree.ElementTree as ET
#import urllib2

##setting proxy utk terhubung via server proksi
#proxy = urllib2.ProxyHandler({"http":"http://karsono.puguh11@mhs.if.its.ac.id:C418_RailF4nZ@proxy.its.ac.id:8080"}) 
#opener = urllib2.build_opener(proxy, urllib2.HTTPHandler)
#urllib2.install_opener(opener)

##alamat web service
client = Client('http://www.webservicex.net/stockquote.asmx?wsdl')
#print client

##input kode saham
stockquote = raw_input('Masukkan kode saham: ')
stock = client.service.GetQuote(stockquote)

##mulai mendapatkan informasi saham
tree = ET.ElementTree(ET.fromstring(stock))

##mendapatkan tag root yg ada pada file XML
root = tree.getroot()

##melakukan parsing XML dan menampilkan hasilnya
for elem in root.iter():

    ##jika isi tag tidak kosong dan terdapat banyak spasi, tampilkan hasil parsing-nya
    ##format tampilan: "nama_tag": "isi_tag"
    if (elem.text is not None and elem.text.strip() != ''):
        print elem.tag, ': ', elem.text

    ##jika sebaliknya, tampilkan baris kosong
    else:
        print ''
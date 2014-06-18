from suds.client import Client
import xml.etree.ElementTree as ET
#import urllib2

##setting proxy utk terhubung via server proksi
#proxy = urllib2.ProxyHandler({"http":"http://karsono.puguh11@mhs.if.its.ac.id:C418_RailF4nZ@proxy.its.ac.id:8080"}) 
#opener = urllib2.build_opener(proxy, urllib2.HTTPHandler)
#urllib2.install_opener(opener)

##alamat web service
client = Client('http://www.webservicex.net/globalweather.asmx?wsdl')
#print client

##input kota dan negara
city_name = raw_input('Masukkan nama kota: ')
country_name = raw_input('Masukkan nama negara: ')
country = client.service.GetCitiesByCountry(country_name)
weather = client.service.GetWeather(city_name, country_name)
weather = weather.encode('utf16')

##mulai mendapatkan informasi cuaca di kota tsb.
tree1 = ET.ElementTree(ET.fromstring(country))
tree2 = ET.ElementTree(ET.fromstring(weather))

##mendapatkan tag root yg ada pada file XML
root1 = tree1.getroot()
root2 = tree2.getroot()

##method 1: GetCitiesByCountry()
print "=== Mendapatkan Data Kota Berdasarkan Negara ==="

##melakukan parsing XML dan menampilkan hasilnya
for elem in root1.iter():
    ##jika isi tag tidak kosong dan terdapat banyak spasi, tampilkan hasil parsing-nya
    ##format tampilan: "nama_tag": "isi_tag"
    if (elem.text is not None and elem.text.strip() != ''):
        print elem.tag, ':', elem.text

    ##jika sebaliknya, tampilkan baris kosong
    else:
        print ''

##method 2: GetWeather()
print "\n=== Mendapatkan Data Cuaca untuk Kota Tertentu ==="

##melakukan parsing XML dan menampilkan hasilnya
for elem in root2.iter():
    ##jika isi tag tidak kosong dan terdapat banyak spasi, tampilkan hasil parsing-nya
    ##format tampilan: "nama_tag": "isi_tag"
    if (elem.text is not None and elem.text.strip() != ''):
        print elem.tag, ':', elem.text

    ##jika sebaliknya, tampilkan baris kosong
    else:
        print ''

#print weather
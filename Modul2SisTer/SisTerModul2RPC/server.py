#impor library bawaan utk RPC (SimpleXMLRPCServer) dan untuk membaca URL/situs di internet (urllib2) 
from SimpleXMLRPCServer import SimpleXMLRPCServer
import urllib2

#fungsi untuk membaca data dari URL
def view():
    response1 = urllib2.urlopen('http://data.bmkg.go.id/1formatXMLCuaca.xml').read()
    response2 = urllib2.urlopen('http://data.bmkg.go.id/alamatbalai.xml').read()
    response3 = urllib2.urlopen('http://data.bmkg.go.id/alamatstasiun.xml').read()
    response4 = urllib2.urlopen('http://data.bmkg.go.id/gempadirasakan.xml').read()
    #pilih salah satu URL yg ingin diuji dng cara mengganti angka di belakang variabel 'response'!
    return response3
  
#server melakukan binding koneksi dari klien dengan waktu yg tak terbatas
server = SimpleXMLRPCServer(("localhost", 5432))
print "Listening on port 5432..."
server.register_function(view, "view")
server.serve_forever()
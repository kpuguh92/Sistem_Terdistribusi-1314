#impor library bawaan utk RPC (SimpleXMLRPCServer) dan untuk parsing XML dari URL/situs di internet (ElementTree) 
import xmlrpclib
import xml.etree.ElementTree as ET

#melakukan koneksi ke server
proxy = xmlrpclib.ServerProxy("http://localhost:5432/")

#memanggil fungsi view() yg ada pada server
a = proxy.view()

#membaca isi file XML
tree = ET.ElementTree(ET.fromstring(a))

#mendapatkan tag root yg ada pada file XML
root = tree.getroot()

#melakukan parsing XML dan menampilkan hasilnya
for elem in root.iter():
    #jika isi tag tidak kosong dan terdapat banyak spasi, tampilkan hasil parsing-nya
    #format tampilan: "nama_tag": "isi_tag"
    if (elem.text is not None and elem.text.strip() != ''):
        print elem.tag, ': ', elem.text
    #jika sebaliknya, tampilkan baris kosong
    else:
        print ''
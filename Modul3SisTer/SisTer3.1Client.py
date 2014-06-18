from suds.client import Client
url = 'http://localhost:8080/Hashing/soap/description'
client = Client(url)

i = raw_input('Pilih Metode : ')
inp = raw_input('Masukan Text : ')

if i=='MD5' :
    result = client.service.MD5(inp)   
elif i=='SHA1' :
    result = client.service.SHA1(inp)
elif i=='SHA224' :
    result = client.service.SHA224(inp)


print result

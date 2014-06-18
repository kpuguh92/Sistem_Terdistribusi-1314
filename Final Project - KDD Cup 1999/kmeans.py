from scipy.cluster.vq import kmeans,vq
import numpy as np
import cPickle as pickle

f = open('kddcup.testdata.unlabeled_10_percent.csv','rb')
o = open('centroid','wb')
contents = map(str.strip, f.readlines())
data = []
for i in contents :
    x = map(float, i.split(','))
    data.append(x)
data = np.array(data)
centroids,_ = kmeans(data, 3)
print centroids
o.write(pickle.dumps(centroids))


def compute(dataTest,centroids):
    import socket
    from scipy.spatial import distance
    
    host = socket.gethostname()
    
    # Inisialisasi kelas Data test ke-i dengan 0
    indexClass = 0
    # Inisialisasi minimal euclidian distance 1 juta 
    minDist = 1000000
    
    # Fungsi euclidian distance untuk mencari jarak terkecil
    for idx, j in enumerate(centroids): 
        dist = distance.euclidean(dataTest,j);
        if dist < minDist:
            minDist = dist
            indexClass = idx
    return (host, indexClass)

if __name__=='__main__':
    import numpy
    import dispy
    import pickle
    import csv
    from scipy.cluster.vq import kmeans
    
    print 'Create job cluster...'
    cluster = dispy.JobCluster(compute)#,nodes=['192.168.56.*'],ip_addr='192.168.56.101')
    
    # Data training digunakan untuk mencari centroid.
    dataTraining = numpy.loadtxt('Data/kddcup.newtestdata_10_percent_unlabeled2', dtype=int, delimiter=',')
    # Data testing digunakan untuk clustering dengan centroid. 
    dataTesting = numpy.loadtxt('Data/kddcup.testdata.unlabeled_10_percent2', dtype=int, delimiter=',')
    
    # Menghitung k-Means with sejumlah K kluster dan menyimpan hasil ke file
    print 'Waiting for getting and reading centroid...'
    K = 21
    listCentroids,_ = kmeans(dataTraining, K)
    pickle.dump(listCentroids, open('Data/Centroid.pickle', "wb"))
    
    # Menyiapkan file penyimpanan hasil kluster k-Means
    resultFile = open('Data/DataTestClusterResult',"wb") 
    writeResult = csv.writer(resultFile, delimiter=",")
    
    # Menyiapkan jumlah client untuk distribusi data dan penampung hasil kluster data
    clientWork = 4                       
    myCluster = []
    
    # Load hasil centroid
    centroids = pickle.load(open('Data/Centroid.pickle',"rb"))
    
    indexDt = 0
    print 'Clustering Data Testing with workers(s)...\n'
    print str('Executor').center(25), str('Job ID').center(20), str('Start Time').center(15), str('Class').center(35)
    while(indexDt != (len(dataTesting))):
        jobs = []
        for n in range(clientWork):
            job = cluster.submit(dataTesting[indexDt],centroids)
            job.id = indexDt
            jobs.append(job)
            indexDt += 1
            
        print '-----------------------------------------------------------------------'
        for job in jobs:
            host, result = job()
            print str(host).ljust(20), str(job.id).rjust(10), str(job.start_time).rjust(20), str(result).rjust(20)
            myCluster.append(result)
     
    # Print tabel hasil dispy   
    cluster.stats()
    
    # Menympan hasil kluster Data Testing
    indexDt=0
    while(indexDt != (len(dataTesting)-1)):
        myPrint = dataTesting[indexDt].tolist()
        myPrint.append(myCluster[indexDt])
        writeResult.writerow(myPrint)
        indexDt += 1

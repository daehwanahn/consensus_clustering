from __future__ import absolute_import, division, print_function, unicode_literals
__metaclass__ = type
import numpy as np
from scipy.spatial import distance as spdist
from timeit import default_timer as timer
from datetime import timedelta
from copy import deepcopy

from consensus_clustering.lib import similarity

#----------------------------
#Consensus Clustering
#----------------------------

class Consensus(object):

    def __init__(self, clusterings, k=5, recluster_what='points', how='spectral', spectral_params=None, verbose=True, **kwargs):

        '''
        Consensus clustering, see e.g. http://www.jmlr.org/papers/volume3/strehl02a/strehl02a.pdf
        
        Args:
            cluterings: a list of clustering objects from the clustertools package (or any other objects with a scikit-learn interface, i.p. a cluster_labels-attribute)
            k=5: number of clusterings to produce
            recluter_what='clusters': whether to recluster the jaccard similarity matrix of the clusters and then competing for points (faster) or a hamming similarity matrix of the points (slower)
            how='spectral':
                'spectral': recluster similarity matrix with spectral clustering. Keyword arguments have to be passed:
                    - (e.g. bandwidth for euclidian metric)
                'hierarchical': recluster similarity matrix with hierarchical clustering. Parameters have to or can be to be passed (stopping criteria)
                    - 
            verbose=True:
                whether to print the verbose data of the single algorithms and some data after termination.
            spectral_params: dict containing optional settings for spectral clustering on similarity matrix, see clustertools.models.similarity.SpectralClustering doc. If None, default settings will be applied. Note that "k" and "verbose" parameter of the SpectralClustering instance will always be determined arguments passed to Consensus. Also, "data" will be the similarity matrix and "similarity_measure" will be None.
        '''
        
        self._clusterings = clusterings
        self._k = k
        self._recluster_what = recluster_what
        self._how = how
        self._verbose = verbose
        self._spectral_params = spectral_params
        self._cluster_labels = None
        self._m = len(clusterings)
        self._anmi = None # average normalized mutual information of consensus
        self._nmi_arr = None # array of all NMI's of the clusterings with consensus
        
        # Safety checks
        assert self._m > 0, "the list of fitted clustering objects is empty"
        self._n = len(clusterings[0].cluster_labels)
        assert any((self._recluster_what == 'clusters', self._recluster_what == 'points')), "unknown self._recluster_what-parameter"
        assert any((self._how == 'spectral', self._how == 'hierarchical')), "unknown how-parameter"
        assert all(tuple([len(clustering.cluster_labels) == self._n for clustering in clusterings])), "clusterings did not cluster same amount of points"
        

    @property
    def cluster_labels(self):
        if self._cluster_labels is None:
            self.fit()
        return self._cluster_labels
    @cluster_labels.setter
    def cluster_labels(self,value):
        self._cluster_labels = value
        
    @property
    def anmi(self):
        if self._anmi is None:
            self.fit()
        return self._anmi
    @anmi.setter
    def anmi(self,value):
        self._anmi = value
        
    @property
    def nmi_arr(self):
        if self._nmi_arr is None:
            self.fit()
        return self._nmi_arr
    @nmi_arr.setter
    def nmi_arr(self,value):
        self._nmi_arr = value

    def fit(self):
        '''
        Runs consensus clustering on labels of given clustering objects.
        '''

        if self._verbose:
            start_time = timer()

        # Init
        self._cluster_labels = np.zeros(self._n)
        for i in range(self._m):
            self._clusterings[i] = self._noise_to_zero(self._clusterings[i])        
        
        # Create array of labels
        labels = np.zeros((self._n, self._m)).astype(int)
        num_unique_labels = np.zeros(self._m).astype(int)
        for j in range(self._m):
            labels[:, j] = self._clusterings[j].cluster_labels
            num_unique_labels[j] = len(np.unique(self._clusterings[j].cluster_labels))
        num_edges = None
        hypergraph = None
        
        # Possibly create hypergraph
        if self._recluster_what == 'clusters':
            num_edges = np.sum(num_unique_labels).astype(int)
            hypergraph = np.zeros((num_edges, self._n)).astype(int)
            cluster_sum = 0
            for j in range(self._m): # for all clusterings
                # for l in range(num_unique_labels[j]): # for all cluster labels NO! See below
                for l, label in enumerate(np.unique(self._clusterings[j].cluster_labels)):
                    hypergraph[cluster_sum + l, :] = (labels[:, j] == label)
                cluster_sum += num_unique_labels[j]
        
        # Calculate distance matrix of hyperedges or points
        if self._recluster_what == 'clusters':
            dists = spdist.squareform(spdist.pdist(hypergraph, 'jaccard'))
        elif self._recluster_what == 'points':
            dists = spdist.squareform(spdist.pdist(labels, 'hamming'))
        
        # Recluster
        meta_clustering = None
        if self._how == 'hierarchical':
            meta_clustering = similarity.HierarchicalClustering(dists, metric=None, k=self._k, verbose=self._verbose)
        if self._how == 'spectral':
            if self._spectral_params is None:
                # Default spectral clustering
                meta_clustering = similarity.SpectralClustering(dists, similarity_measure=None, k=self._k, verbose=self._verbose)
            else:
                self._spectral_params["data"] = dists
                self._spectral_params["similarity_measure"] = None
                self._spectral_params["k"] = self._k
                self._spectral_params["verbose"] = self._verbose
                meta_clustering = similarity.SpectralClustering(**self._spectral_params)
        meta_clustering.fit()
        meta_labels = meta_clustering.cluster_labels
        
        # Take the meta_cluster_labels, or compete for points when reclustering clusters
        if self._recluster_what == 'points':
            self._cluster_labels = meta_labels
        if self._recluster_what == 'clusters':
            point_label_buckets = np.zeros((self._n, self._k)).astype(int)
            for edge_ind in range(num_edges):
                point_label_buckets[:, meta_labels[edge_ind]] += hypergraph[edge_ind]
            for i in range(self._n):
                self._cluster_labels[i] = np.random.choice(np.flatnonzero(point_label_buckets[i, :] == point_label_buckets[i, :].max()))
        # self._cluster_labels = np.array(self._cluster_labels)
        
        # Compute ANMI and NMI-array (average normalized mutual information) of consensus with clusterings
        self._anmi = 0
        self._nmi_arr = np.zeros(self._m)
        for i, clustering in enumerate(self._clusterings):
            nmi = self.compute_nmi(self.cluster_labels, clustering.cluster_labels)
            # self._anmi += nmi
            self._nmi_arr[i] = nmi
        self._anmi = np.sum(self._nmi_arr) / self._m

        # Algorithm verbosity
        if self._verbose:
            print("Consensus clustering terminated.")
            elapsed_time = timer() - start_time
            elapsed_time = timedelta(seconds=elapsed_time)
            print("Finished after " + str(elapsed_time))
            print("ANMI (average normalized mutual information) of consensus with clusterings: {:.3}".format(self._anmi))
            
    
    def compute_nmi(self, labels_a, labels_b):
        '''
        Computes the normalized mutual information between two clusterings.
        
        Args:
            labels_a: labels of clustering a
            labels_b: labels of clustering b
            
        Output:
            normalized mutual information
        '''
        
        assert len(labels_a) == len(labels_b), "clusterings did not cluster same amount of points"
        
        # Init
        n = len(labels_a)
        # k_a = len(np.unique(labels_a))
        # k_b = len(np.unique(labels_b))
        a_normalizer = 0 # negative entropy of clustering A, but with n cancelled
        b_normalizer = 0
        
        # Compute mutual information estimate and entropy estimate of a (with n cancenlled)
        mutual_information = 0
        # for i in range(k_a):
        for a in np.unique(labels_a):
            n_a = sum(labels_a == a)
            if n_a > 0:
                a_normalizer += n_a * np.log(n_a / n)
                # for j in range(k_b):
                for b in np.unique(labels_b):
                    n_b = sum(labels_b == b)
                    n_ab = sum((labels_a == a) * (labels_b == b))
                    if n_ab > 0:
                        mutual_information += n_ab * np.log(n * n_ab / n_a / n_b)
                
        # Compute entropy estimate of b (with n cancenlled)
        for b in np.unique(labels_b):
            n_b = sum(labels_b == b)
            if n_b > 0:
                b_normalizer += n_b * np.log(n_b / n)
            
        # print("mutual information: {0}".format(mutual_information))
        # print("entropy-normalizer a: {0}".format(a_normalizer))
        # print("entropy-normalizer b: {0}".format(b_normalizer))
        
        result = mutual_information
        if a_normalizer * b_normalizer > 0:
            result /= np.sqrt(a_normalizer * b_normalizer)
        
        return result
        
    def nmi_with_me(self, labels):
        if self.cluster_labels is None:
            self.fit()
        return self.compute_nmi(self.cluster_labels, labels)
    
    def _noise_to_zero(self, clustering_obj):
        '''Returns a copy of the clustering object with noise-labels set to an ndarray of 0-labels.'''
        clustering_obj_copy = deepcopy(clustering_obj)
        for i in range(len(clustering_obj_copy.cluster_labels)):
            if clustering_obj_copy.cluster_labels[i] == 'noise':
                clustering_obj_copy.cluster_labels[i] = 0
            # if clustering_obj_copy.cluster_labels[i] == -1:
                # clustering_obj_copy.cluster_labels[i] = 0
        clustering_obj_copy.cluster_labels = np.array(clustering_obj_copy.cluster_labels)
        return clustering_obj_copy

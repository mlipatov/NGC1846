# Utilities needed to convolve, downsample, normalize and evaluate probability densities on grids
# of observables
import config as cf
from lib import plot_util as plt
import numpy as np
import time
from scipy.interpolate import interp1d

dims = ['mag', 'col', 'vsini']
dimensions = ['magnitude', 'color', r'$v\,\sin{i}$']

# obtain the dependence of probability leakage on standard deviations of the Gaussian kernels
# convolve the prior with a number of Gaussian kernels on the grid of observables without downsampling
# Inputs:
# 	probability density on a grid of observables
# 	number of standard deviations to extend Gaussian kernels
# Output:
#	a list of two-element lists specifying the slope and y-intercept of log-log fits for each observable
def dP_sigma(density, nsig, prefix, suffix, age, Z):
	start = time.time()
	# standard deviations in units of fine grid step size, from one step size to the size
	# that can have half a kernel fit at an edge of the ROI 
	s = np.linspace(0.5, 3, 6)
	kernels = [Kernel(s[j], nsig) for j in range(s.shape[0])]
	sigma = np.outer(density.step, s)	
	dP = [] # a list of total probability change within the RON vs sigma, one for each observable
	for i in range(len(density.obs)): # for each observable dimension except vsini
		dp = []
		for j in range(len(kernels)): # for each kernel width
			kernel = kernels[j]
			d = density.copy()
			# check that the kernel, evaluated at the ROI boundaries, fits within the grid
			if d.check(i, kernel, cf.ROI[i]): 
				d.convolve(i, kernel)
				dp.append(d.integrate(cf.RON) - 1)
		# the dependence of log probability change on log sigma, in units of the observable
		dp = np.array(dp)
		# if the order of the probability change is comparable with precision
		if -np.log10(np.abs(dp).max()) > np.finfo(float).precision / 2:  
			fit = None
		else:
			x = sigma[i][:dp.shape[0]]
			fit = interp1d(x, dp, kind='cubic', fill_value='extrapolate')
		dP.append( fit )
		plt.dP_sigma(x, y, fit, prefix, suffix, age, Z, dimensions[i], dims[i])
	print( str(time.time() - start) + ' seconds.' )
	return dP

# a finite symmetric Gaussian probability density kernel on a discrete, evenly spaced grid
class Kernel:

	# Inputs: 
	#	standard deviation of the kernel, in units of step size
	#	lower bound on the extent of downsampled kernel, in standard deviations
	#	downsample factor that will be used when convolving with this kernel
	def __init__(self, sigma, nsig, ds:int=1):
		# number of steps in one half of the kernel
		n = np.ceil(nsig * sigma / ds).astype(int) * ds
		x = np.linspace(-n, n, 2 * n + 1) # abscissae
		y = np.exp(-0.5 * (x/sigma)**2)
		self.y = y / np.sum(y) # normalize the kernel
		self.n = n

# probability density on a discrete, evenly spaced grid of observables;
# all integration (including marginalization, convolution and normalization) 
#	assumes unit discrete steps;
# scale (divide) by the product of grid steps to get meaningful probability densities
class Grid:

	# Inputs:
	# 	density on a grid of observables, an n-dimensional array
	# 	1D grids of observables, a list of n arrays
	def __init__(self, dens, obs, age, Z):
		self.dens = dens 
		self.obs = obs 
		self.dim = len(obs) # number of dimensions, e.g. 3
		# discrete step in each dimension
		step = []
		for i in range(len(obs)):
			step.append( obs[i][1] - obs[i][0] )
		self.step = np.array(step)
		self.age = age
		self.Z = Z

	def copy(self):
		dens = np.copy(self.dens) 
		obs = []
		for o in self.obs:
			obs.append(np.copy(o))
		return Grid(dens, obs, self.age, self.Z)

	# check if the density can be convolved in some dimension with a symmetric kernel
	# so that the calculated points cover some region
	# Inputs:
	#	axis to convolve
	#	convolving kernel
	#	region of the focal observable dimension that should be calculated
	def check(self, axis, kernel, region):
		obs = self.obs[axis]
		index = np.nonzero( (obs >= region[0]) & (obs <= region[1]) )[0]
		# see if the number of steps in half the kernel left of the left boundary of the region
		# drops below smallest index (zero) and similarly for the largest index
		return (index.min() >= kernel.n) & (len(obs) - index.max() > kernel.n) 

	# convolve and downsample at the same time
	# Inputs: 
	#	axis along which to convolve
	# 	a kernel to convolve with
	#	downsample factor (an integer)
	# Notes:
	# 	number of steps in one half of the kernel must be a multiple of the downsample factor 
	def convolve(self, axis, kernel, ds:int=1):
		dens = np.moveaxis(self.dens, axis, -1) # move the focal axis to the front
		# shape of the result grid, downsampled in the focal dimension
		shape = list(dens.shape)
		shape[-1] = shape[-1] // ds 
		res = np.zeros(shape) # initialize the result grid
		# number of steps in one half of the kernel, downsampled
		j_lim = kernel.n // ds 
		# number of calculated downsampled points
		n_max = shape[-1] - 2*j_lim 
		# convolve
		for j in range(kernel.n * 2 + 1):
		    res[..., j_lim:-j_lim] += kernel.y[j] * dens[...,j::ds][...,:n_max]
		# remove the strip where the convolution wasn't computed
		res = res[...,j_lim:-j_lim]
		# move the focal axis back in its place
		self.dens = np.moveaxis(res, -1, axis)
		# remove the strip where the convolution wasn't computed from the focal observable's grid, 
		# then downsample the grid and limit it to computed points only
		self.obs[axis] = self.obs[axis][kernel.n:-kernel.n][::ds][:n_max]
		# multiply step size in the focal dimension by the downsample factor
		self.step[axis] *= ds

	def marginalize(self, axis):
		weights = np.ones_like(self.obs[axis])
		weights[0] = weights[-1] = 1./2
		dens = np.moveaxis(self.dens, axis, -1) # move the focal axis to the front
		self.dens = np.sum(weights * dens, axis=-1) # integrate
		self.obs.pop(axis)
		self.step = np.delete(self.step, axis)
		self.dim -= 1

	# weights for integrating on an n-dimensional region within this n-dimensional grid
	# Inputs:
	#	boundaries of an n-dimensional rectangular region, an array of two-element arrays
	# Notes:
	#	the dimensionality of the region has to be the same as that of the density grid
	def weights(self, region):
		for i in range(region.shape[0]):
			index = np.nonzero( (self.obs[i] >= region[i][0]) & (self.obs[i] <= region[i][1]) )[0]
			w = np.zeros_like( self.obs[i] )
			w[index] = 1.
			w[index.min()] = w[index.max()] = 1./2
			if i == 0:
				weights = w
			else:
				weights = np.multiply.outer(weights, w)	
		return weights

	# integrate on a region
	def integrate(self, region):
		return np.sum(self.dens * self.weights(region))

	# normalize on a region and scale for plotting or probability calculation
	def normalize(self, region):
		self.dens /= self.integrate(region)

	# divide by the product of grid steps to get properly scaled probability
	def scale(self):
		self.dens /= np.prod(self.step)

	# integrate the product of a Gaussian kernel centered on a data point and 
	# probability density in some dimension
	# Inputs:
	#	dimension of integration
	#	standard deviation of the kernel in the units of the focal observable
	#	center of the kernel, also the value of the observable at the data point
	# Output: probability density on a grid without the focal dimension
	# Notes: if both the density and the kernel are normalized, 
	#		the result is an evaluation of a normalized probability distribution, 
	#		except for de-normalization due to the implied convolution; 
	#	additionally, either the input density should be scaled after normalization,
	#		or the output should be scaled.
	def integrate_kernel(self, axis, sigma, nsig, point):
		dens = np.moveaxis(self.dens, axis, -1) # move the focal axis to the front
		obs = self.obs[axis] # get the focal observable
		step = self.step[axis] # the step in the focal dimension
		w = np.ones_like(obs); w[0] = w[-1] = 1./2 # weights for integrating 
		# if the residual standard deviation is small in comparison with grid step size
		if (sigma / step < 1./2):
			# interpolate
			self.dens = interp1d( obs, dens, axis=-1 )(point) 
		else: 
			# compute the kernel around the data point
			x = (obs - point) / sigma
			kernel = np.exp(-x**2 / 2.)
			kernel[np.abs(x) > nsig] = 0 # sigma cutoff for symmetry
			kernel /= np.sum(kernel) # normalize the kernel
			# convolve
			self.dens = np.sum(w * kernel * dens, axis=-1)
		self.obs.pop(axis)
		self.step = np.delete(self.step, axis)
		self.dim -= 1
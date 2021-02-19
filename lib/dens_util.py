# Utilities needed to convolve, downsample, normalize and evaluate probability densities on grids
# of observables
import config as cf
import numpy as np
import numba as nb
from scipy.ndimage import convolve1d
from scipy.interpolate import interp1d
import copy

class ConvolutionException(Exception):
    pass

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
# all integration (including marginalization, normalization and convolution at one or all points) 
#	assumes unit discrete steps;
# after all such integration, and before evaluation at a specific point, 
# scale (divide) by the product of grid steps to get meaningful probability densities;
# two types of dimensions:
# 	1. normalized over the finite region of interest (ROI),
#	2. normalized over all reals.
class Grid:
	# Inputs:
	# 	density on a grid of observables, an n-dimensional array
	# 	1D grids of observables, a list of n arrays
	# 	finite region of interest, a list of two-element lists
	#	list of the kind of each observable: 
	#		True for normalized over ROI, False for collected at the ROI boundaries
	# 	age, metallicity
	def __init__(self, dens, obs, ROI, norm, age, Z):
		self.dens = dens 
		self.obs = obs 
		self.dim = len(obs) # number of dimensions, e.g. 3
		# discrete step in each dimension
		step = []
		for i in range(len(obs)):
			step.append( obs[i][1] - obs[i][0] )
		self.step = np.array(step)
		self.ROI = ROI
		self.norm = norm
		# a list of dependences of de-normalization on
		#		the standard deviation of the convolving kernel (a spline function)
		self.correction = [None] * len(obs)
		self.age = age
		self.Z = Z

	def copy(self):
		dens = np.copy(self.dens) 
		obs = []
		for o in self.obs:
			obs.append(np.copy(o))
		ROI = copy.deepcopy(self.ROI)
		norm = copy.deepcopy(self.norm)
		density = Grid(dens, obs, ROI, norm, self.age, self.Z)
		density.correction = copy.deepcopy(self.correction)
		return density

	def remove_axis(self, axis):
		self.obs.pop(axis)
		self.norm.pop(axis)
		self.correction.pop(axis)
		self.ROI = np.delete(self.ROI, axis, axis=0)
		self.step = np.delete(self.step, axis)
		self.dim -= 1

	# obtain the dependence of probability leakage on standard deviations of the Gaussian kernels
	# convolve the density with a number of Gaussian kernels on the grid of observables without downsampling
	# Inputs:
	# 	number of standard deviations to extend Gaussian kernels
	# Outcome:
	#	for normalized dimensions, updates the dependence of de-normalization on kernel standard deviation
	# Notes:
	#	operates on normalized, un-scaled probability density
	def dP_sigma(self, nsig):
		dmar = self.copy()
		for i in range(len(self.obs)): # for each observable dimension
			if not self.norm[i]: # if the dimension is not normalized in the ROI
				dmar.marginalize(i) # marginalize it
		# standard deviations in units of grid step size, up to the size
		# that can have half a kernel fit at an edge of the ROI 
		s = np.linspace(0.5, cf.conv_err, 9)
		kernels = [Kernel(s[j], nsig) for j in range(s.shape[0])]
		sigma = np.outer(self.step, s)	
		for i in range(len(self.obs)): # for each observable dimension
			if self.norm[i]: # if the dimension is normalized in the ROI
				dp = []
				for j in range(len(kernels)): # for each kernel width
					kernel = kernels[j]
					d = dmar.copy()
					# check that the kernel, evaluated at the ROI boundaries, fits within the grid
					if d.check_roi(i, kernel): 
						d.convolve(i, kernel)
						d.integrate_ron()
						dp.append(d.dens - 1)
				# the dependence of log probability change on log sigma, in units of the observable
				dp = np.array(dp)
				x = sigma[i][:dp.shape[0]]
				fit = interp1d(x, dp, kind='cubic', fill_value='extrapolate') # extrapolate for sigma closer to zero
				# if maximum probability change is less than the cube root of precision (< 10^-5 for regular floats)
				if -np.log10(np.abs(dp).max()) > np.finfo(float).precision / 3:  
					self.correction[i] = None
				else:
					self.correction[i] = fit

	# check if the density can be convolved in some dimension with a symmetric kernel
	# so that the calculated points cover the region of interest
	# Inputs:
	#	axis to convolve
	#	convolving kernel
	#	region of the focal observable dimension that should be calculated
	def check_roi(self, axis, kernel):
		obs = self.obs[axis]
		index = np.nonzero( (obs >= self.ROI[axis][0]) & (obs <= self.ROI[axis][1]) )[0]
		# see if the number of steps in half the kernel left of the left boundary of the region
		# drops below smallest index (zero) and similarly for the largest index
		return (index.min() >= kernel.n) & (len(obs) - index.max() > kernel.n) 

	# convolve, then downsample;
	# uses ndimage library routines for convolution;
	# ~6 seconds for NGC1846 with downsample = 3.
	# Inputs: 
	#	axis along which to convolve
	# 	a kernel to convolve with
	#	downsample factor (an integer)
	# Notes:
	# 	number of steps in one half of the kernel must be a multiple of the downsample factor 
	def convolve(self, axis, kernel, ds:int=1):
		dens = np.moveaxis(self.dens, axis, -1) # move the focal axis to the front
		dens = convolve1d(dens, kernel.y, axis=-1, mode='constant')
		# remove the strip on each side equal to one half of the kernel, where there are edge effects
		dens = dens[..., kernel.n:-kernel.n]
		obs = self.obs[axis][kernel.n:-kernel.n]
		# downsample, move the focal axis back in its place and set
		self.dens = np.moveaxis(dens[..., ::ds], -1, axis)
		self.obs[axis] = obs[::ds]
		# multiply step size in the focal dimension by the downsample factor
		self.step[axis] *= ds

	# convolve, then downsample;
	# uses numpy library routines for convolution;
	# ~13 seconds for NGC1846 with downsample = 3.
	# Inputs: 
	#	axis along which to convolve
	# 	a kernel to convolve with
	#	downsample factor (an integer)
	# Notes:
	# 	number of steps in one half of the kernel must be a multiple of the downsample factor 
	def convolve_numpy(self, axis, kernel, ds:int=1):
		dens = np.moveaxis(self.dens, axis, -1) # move the focal axis to the front
		dens1 = np.zeros_like(dens)
		for index, d in np.ndenumerate(dens[..., 0]):
			dens1[index] = np.convolve(dens[index], kernel.y, mode = 'same')
		# remove the strip on each side equal to one half of the kernel, where there are edge effects
		dens = dens1[..., kernel.n:-kernel.n]
		obs = self.obs[axis][kernel.n:-kernel.n]
		# downsample, move the focal axis back in its place and set
		self.dens = np.moveaxis(dens[..., ::ds], -1, axis)
		self.obs[axis] = obs[::ds]
		# multiply step size in the focal dimension by the downsample factor
		self.step[axis] *= ds

	# convolve and downsample at the same time; 
	# use a custom routine that only computes the result at downsampled locations
	# ~15 seconds for NGC1846 with downsample = 3; 
	# should be more efficient for larger downsampling factors.
	# Inputs: 
	#	axis along which to convolve
	# 	a kernel to convolve with
	#	downsample factor (an integer)
	# Notes:
	# 	number of steps in one half of the kernel must be a multiple of the downsample factor 
	def convolve_downsample(self, axis, kernel, ds:int=1):
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

	# weights on a 1D region according to a simple Riemann sum
	def w(self, region, axis):
		obs = self.obs[axis]
		index = np.nonzero( (obs >= region[0]) & (obs <= region[1]) )[0]
		w = np.zeros_like( obs )
		w[index] = 1.
		return w

	# integral on a 1D region
	def integrate(self, region, axis):
		w = self.w(region, axis)
		# move the focal axis to the front
		dens = np.moveaxis(self.dens, axis, -1) 
		# integrate density
		self.dens = np.sum(w * dens, axis=-1)
		self.remove_axis(axis)

	# integrate the density beyond one of the ROI boundaries along a given dimension
	def integrate_lower(self, axis):
		self.integrate([-np.inf, self.ROI[axis][0]], axis)
	def integrate_upper(self, axis):
		self.integrate([self.ROI[axis][1], np.inf], axis)

	# marginalize in one of the dimensions; 
	# overall density stays normalized if the dimension is not normalized over the ROI
	def marginalize(self, axis):
		self.integrate([-np.inf, np.inf], axis)

	# integrate on the region of normalization
	def integrate_ron(self):
		while self.dim > 0:
			if self.norm[-1]: region = self.ROI[-1]
			else: region = [-np.inf, np.inf]
			self.integrate(region, -1)

	# normalize
	def normalize(self):
		d = self.copy()
		# integrate
		d.integrate_ron()
		norm = d.dens
		self.dens /= norm

	# return properly scaled probability density
	def density(self):
		return self.dens / np.prod(self.step)
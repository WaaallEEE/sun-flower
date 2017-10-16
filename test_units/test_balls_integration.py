from importlib import reload
import numpy as np
import balltracking.balltrack as blt
import matplotlib
import matplotlib.pyplot as plt
from timeit import timeit
import fitstools
import fitsio
import filters

def wrapper(func, *args, **kwargs):
    def wrapped():
        return func(*args, **kwargs)
    return wrapped

### Test and benchmark of the integration on a generic gaussian data surface

# Create a generic surface
size = 50
sigma = 2
surface = blt.gauss2d(size, sigma).astype(np.float32) * 3
# Set ball parameters, the number of frames nt is ignored in this benchmar
rs = float(2.0)
dp = float(0.2)
nt = 50
# Instatiate the BT class with the gaussian generic surface dimensions
bt = blt.BT(surface.shape, nt, rs, dp)

# Initialize 1 ball
xstart = np.array([20], dtype=np.float32)
ystart = np.array([24], dtype=np.float32)
zstart = blt.put_balls_on_surface(surface, xstart, ystart, rs, dp)

# Try with python/numpy interpolation
pos, vel = blt.initialize_ball_vector(xstart, ystart, zstart)
pos, vel, force = [np.array(v).squeeze() for v in zip(*[blt.integrate_motion0(pos, vel, bt, surface) for i in range(nt)])]
# Try with cython
pos2, vel2 = blt.initialize_ball_vector(xstart, ystart, zstart)
pos2, vel2, force2 = [np.array(v).squeeze() for v in zip(*[blt.integrate_motion(pos2, vel2, bt, surface) for i in range(nt)])]


# Display and compare results
f1 = plt.figure(figsize=(10, 10))
plt.imshow(surface, origin='lower', cmap='gray')
plt.plot(xstart, ystart, 'r+', markersize=10)

plt.plot(pos[:,0], pos[:,1], 'go', markerfacecolor='none')
plt.plot(pos2[:,0], pos2[:,1], 'b+', markerfacecolor='none')


# Profile

xstart = np.full([16129], 20, dtype=np.float32)
ystart = np.full([16129], 30, dtype=np.float32)
zstart = blt.put_balls_on_surface(surface, xstart, ystart, rs, dp)

pos, vel = blt.initialize_ball_vector(xstart, ystart, zstart)
mywrap = wrapper(blt.integrate_motion0, pos, vel, bt, surface)
print(timeit(mywrap, number = 100))

pos2, vel2 = blt.initialize_ball_vector(xstart, ystart, zstart)
mywrap2 = wrapper(blt.integrate_motion, pos2, vel2, bt, surface)
print(timeit(mywrap2, number = 100))


#### Test real case integration
file = '/Users/rattie/Data/SDO/HMI/EARs/AR12673_2017_09_01/series_continuum/mtrack_20170901_000000_TAI20170905_235959_LambertCylindrical_continuum_00000.fits'
#file = '/Users/rattie/Data/SDO/HMI/EARs/AR12673_2017_09_01/series_continuum_calibration/calibration/drift0.20/drift_0001.fits'
# Get the header
h       = fitstools.fitsheader(file)
# Get the 1st image
image   = fitsio.read(file).astype(np.float32).copy(order='C')
# Filter image
ffilter_hpf = filters.han2d_bandpass(image.shape[0], 0, 5)
fdata_hpf = filters.ffilter_image(image, ffilter_hpf)
sigma = fdata_hpf[1:200, 1:200].std()

surface = blt.rescale_frame(fdata_hpf, 2*sigma).astype(np.float32)

nt = 15
rs = 2
dp = 0.2
bt = blt.BT(image.shape, nt, rs, dp)
# Initialize ball positions
bt.initialize_ballpos(surface)

# integrate motion over some time steps. Enough to have overpopulated cells (bad balls).
pos, _, _ = [np.array(v).squeeze().swapaxes(0,1) for v in zip(*[blt.integrate_motion(bt.pos_t, bt.vel_t, bt, surface) for i in range(nt)])]







plt.figure(1)
# image
plt.imshow(surface, cmap='gray', origin='lower', vmin=-1, vmax=1)
# initial positions
plt.plot(bt.xstart, bt.ystart, 'r.', markersize=4)
plt.axis([0, 60, 0, 60])

# # Last position
plt.plot(pos[0, -1, :], pos[1, -1, :], 'c.', markersize=4)

# ball labels
#bnumbers = np.char.mod('%d', np.arange(bt.nballs))
# for x,y,s in zip(pos[0, -1, :], pos[1, -1, :], bnumbers):
#     plt.annotate('%s'%s, xy=(x,y), textcoords='data')

plt.savefig('/Users/rattie/Dev/sdo_tracking_framework/figures/fig_labels.png')
plt.close()

#plt.text(x, y, s, color='red', clip_on=True)
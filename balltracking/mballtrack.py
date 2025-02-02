import os
import sys
import numpy as np
from numpy import pi
from pathlib import Path
import matplotlib.pyplot as plt
from skimage.feature import peak_local_max
from skimage.morphology import disk
from skimage.segmentation import find_boundaries, watershed
import fitstools
import balltrack as blt

DTYPE = np.float32


class MBT:

    def __init__(self, rs=2, am=1, dp=0.3, td=None, tdx=5, tdy=5, zdamping=1,
                 ballspacing=10, intsteps=15, nt=1, mag_thresh=30, noise_level=20, polarity=1,
                 init_pos=None, track_emergence=False, datafiles=None, data=None, prep_function=None, local_min=False,
                 roi=None, fig_dir=None, do_plots=0, axlims=None, figsize=None, fig_vmin_vmax=None, astropy=False,
                 outputdir = None, verbose=True):

        """ Main class for Magnetic Balltracking

        Args:
            rs (int): balls radius in pixels
            am (float) Acceleration factor
            dp (float): Characteristic percentage depth. 0 < dp < 1
            td (float): damping term, in units of time intervals between frames
            tdx (float): damping term in the x-axis, in units of time intervals between frames
            tdy (float): damping term in the y-axis, in units of time intervals between frames
            zdamping (float): damping in the z-axis, in units of time intervals between frames
            ballspacing (int): nb of pixels between balls center at the initialization stage
            intsteps (int): nb of intermediate frames using linear interpolation.
            nt (int): nb of frames to track
            mag_thresh (int): magnetogram pixel threshold above which local extrema are search at initialization
            noise_level (int): magnetogram pixel threshold below which the tracking stops for a ball centered on that pixel
            polarity (int): magnetic polarity (1 or -1 for pos/neg) of the magnetic elements that are to be tracked
            init_pos (ndarray): initial positions of the balls
            track_emergence (bool): enable/disable the tracking of new feature appearing after the 1st frame
            datafiles (list): list of FITS file paths
            data (ndarray): instead of providing a list of files with datafiles, one can directly provide a 3D array
            prep_function (function): function to use for preprocessing an image. Will only accept an image as argument
            local_min (bool): True / False, resp., will track for local minima / maxima, resp.
            roi (tuple): (ymin, ymax, xmin, xmax) of a fixed region of interest in the images for faster computation
            fig_dir (str): directory path for storing figures
            do_plots (int): set to 0: figure export (default), or 1:every new image, or 2:every intermediate steps
            axlims (tuple or list): axis limits in px when plotting the balls over the image [left, right, bottom, top]
            figsize (tuple): size of the figure for the plots of the balls over the image
            fig_vmin_vmax (tuple): percentiles for calculating vmin and vmax for the imshow() function
            astropy (bool): False will use the fitsio package for fits files. fitsio package does not work well on Windows.
            verbose (bool): toggles verbosity
        """

        self.rs = rs
        self.am = am
        self.dp = dp
        self.datafiles = datafiles
        self.astropy = astropy
        self.data = data
        self.intsteps = intsteps
        self.nt = nt
        # data prep parameters
        if prep_function is None:
            prep_function = prep_data
        self.prep_function = prep_function
        self.local_min = local_min
        # Ballspacing is the minimum initial distance between the balls.
        self.ballspacing = ballspacing
        self.polarity = polarity
        # Region of Interest
        self.roi = roi
        # Load 1st image
        self.image = load_data(self.datafiles, 0, astropy=self.astropy, roi=roi)
        self.surface = self.prep_function(self.image)
        self.init_surface = self.surface

        self.nx = self.image.shape[1]
        self.ny = self.image.shape[0]

        # Force scaling factor
        self.k_force = self.am / (self.dp**2 * pi * self.rs**2)
        # Damping
        if td is not None:
            self.tdx = td
            self.tdy = td
        else:
            self.tdx = tdx
            self.tdy = tdy
        self.zdamping = zdamping

        # Precalculate some damping terms, as they can be used many times
        self.e_tdx_ = np.exp(-1 / self.tdx)
        self.e_tdy_ = np.exp(-1 / self.tdy)
        self.e_tdz_ = np.exp(-1 / self.zdamping)

        # Dimensions of the coarse grid. Used only to remove balls when they are too close to each other
        self.nxc_, self.nyc_ = np.ceil(self.nx / self.ballspacing).astype(int), np.ceil(self.nx / self.ballspacing).astype(int) #self.coarse_grid.shape

        # Deepest height below surface level at which ball can fall down.
        self.min_ds_ = 4 * self.rs
        # Maximum number of balls that can possibly be used
        if init_pos is None:
            self.nballs_max = int(self.nx * self.ny / 2)
        else:
            self.nballs_max = init_pos.shape[1]

        # Current position, force and velocity components, updated after each frame
        self.pos = np.full([3, self.nballs_max], -1, dtype=DTYPE)
        self.vel = np.zeros([3, self.nballs_max], dtype=DTYPE)
        self.force = np.zeros([3, self.nballs_max], dtype=DTYPE)
        # Array of the lifetime (age) of the balls
        self.balls_age = np.ones([self.nballs_max], dtype=np.uint32)
        # Storage arrays of the above, for all time steps
        self.ballpos = np.zeros([3, self.nballs_max, self.nt], dtype=DTYPE)
        self.balls_age_t = np.ones([self.nballs_max, self.nt], dtype=np.uint32)
        # Store intermediate positions, force and velocity
        self.ballpos_inter = np.zeros([3, self.nballs_max, self.intsteps])
        self.vel_inter = np.zeros([3, self.nballs_max, self.intsteps])
        self.force_inter = []

        # Ball grid and mesh. Add +1 at the np.arange stop for including right-hand side boundary
        self.ballgrid = np.arange(-self.rs, self.rs + 1, dtype=DTYPE)
        self.ball_cols, self.ball_rows = np.meshgrid(self.ballgrid, self.ballgrid)
        self.bcols = self.ball_cols.ravel()[:, np.newaxis].astype(DTYPE)
        self.brows = self.ball_rows.ravel()[:, np.newaxis].astype(DTYPE)
        self.ds = np.zeros([self.bcols.shape[0]], dtype=DTYPE)

        # Mask of bad balls
        self.bad_balls_mask = np.zeros(self.nballs_max, dtype=bool)
        # Mask of valid balls
        self.valid_balls_mask_t = np.ones([self.nballs_max, self.nt], dtype=bool)

        # Data dependent parameters
        self.noise_level = noise_level
        self.mag_thresh = mag_thresh
        self.track_emergence = track_emergence

        if init_pos is None:
            # Initialization of ball positions
            self.xstart, self.ystart = get_local_extrema(self.image, self.polarity, self.ballspacing, self.mag_thresh,
                                                         local_min=self.local_min)
        else:
            self.xstart = init_pos[0, :]
            self.ystart = init_pos[1, :]
            
        self.zstart = blt.put_balls_on_surface(self.surface, self.xstart, self.ystart, self.rs, self.dp)
        
        self.nballs = self.xstart.size
        self.pos[0, 0:self.nballs] = self.xstart.copy()
        self.pos[1, 0:self.nballs] = self.ystart.copy()
        self.pos[2, 0:self.nballs] = self.zstart.copy()
  
        self.new_valid_balls_mask = np.zeros([self.nballs_max], dtype=bool)
        self.new_valid_balls_mask[0:self.nballs] = True
        self.unique_valid_balls = np.arange(self.nballs)
        self.do_plots = do_plots
        self.axlims = axlims
        self.figsize = figsize
        self.fig_vmin_vmax = fig_vmin_vmax
        self.fig_dir = fig_dir
        self.outputdir = outputdir
        self.verbose = verbose

        self.nbadballs = 0

    def track_all_frames(self):
        """Run Magnetic Balltracking on the self.nt images"""

        if self.verbose:
            print(f'Tracking with {self.nballs} initial balls')
        if self.do_plots:
            os.makedirs(self.fig_dir, exist_ok=True)

        for n in range(0, self.nt):

            if self.verbose:
                print(f'Frame n={n}: {str(self.datafiles[n])}')

            # Load data at current time and next time step for intermediate-step interpolation.
            if self.data is None:
                self.image = load_data(self.datafiles, n, astropy=self.astropy, roi=self.roi)
            else:
                self.image = self.data[:, :, n]
            self.surface = self.prep_function(self.image)

            if n < self.nt - 1:
                if self.data is None:
                    next_image = load_data(self.datafiles, n + 1, astropy=self.astropy, roi=self.roi)
                else:
                    next_image = self.data[:, :, n + 1]
                next_surface = self.prep_function(next_image)
            else:
                next_surface = self.surface

            if self.track_emergence and n > 0:
                self.populate_emergence()

            surface_i = self.surface.copy()
            for i in range(self.intsteps):
                # print('intermediate step i=%d'%i)
                if self.do_plots == 2:
                    if self.polarity == 1:
                        fig_path = Path(self.fig_dir, f'frame_pos_{n:04d}_{i:02d}.png')
                    else:
                        fig_path = Path(self.fig_dir, f'frame_neg_{n:04d}_{i:02d}.png')

                    plot_balls_over_frame(surface_i, self.pos[0, :], self.pos[1, :], fig_path,
                                          z=self.pos[2, :],
                                          figsize=self.figsize, axlims=self.axlims,
                                          title=f'Frame #{n} - step #{i:02d}', cmap='gray',
                                          vmin=self.fig_vmin_vmax[0], vmax=self.fig_vmin_vmax[1])
                # Linearly interpolate surface at intermediate time steps: no effect at the last frame
                surface_i = (self.surface*(self.intsteps - i) + next_surface * i)/self.intsteps
                blt.integrate_motion(self, surface_i)

            # set_bad_balls(self, self.pos)
            self.set_bad_balls()
            self.nbadballs = self.bad_balls_mask.sum()
            # Flag the bad balls with -1
            self.pos[:, self.bad_balls_mask] = -1
            self.vel[:, self.bad_balls_mask] = np.nan
            self.balls_age[self.new_valid_balls_mask] += 1

            self.ballpos[..., n] = self.pos.copy()
            self.balls_age_t[:, n] = self.balls_age.copy()
            self.valid_balls_mask_t[:,n] = self.new_valid_balls_mask

            if self.do_plots == 1:
                if self.polarity == 1:
                    fig_path = Path(self.fig_dir, f'frame_pos_{n:04d}.png')
                else:
                    fig_path = Path(self.fig_dir, f'frame_neg_{n:04d}.png')

                ballvel = None
                if n > 0:
                    ballvel = (self.ballpos[:, :, n] - self.ballpos[:, :, n-1])

                plot_balls_over_frame(self.image, self.pos[0, :], self.pos[1, :], fig_path,
                                      figsize=self.figsize, cmap='gray_r', axlims=self.axlims, ballvel=ballvel,
                                      title=f'Frame # {n}', vmin=self.fig_vmin_vmax[0], vmax=self.fig_vmin_vmax[1])


        # Trim the array down to the actual number of balls used so far.
        # That number has been incremented each time new balls were added, in self.populate_emergence
        if self.verbose:
            print(f'Total number of balls used self.nballs = {self.nballs}')
        self.ballpos = self.ballpos[:, 0:self.nballs, :]
        self.balls_age_t = self.balls_age_t[0:self.nballs, :]
        self.valid_balls_mask_t = self.valid_balls_mask_t[0:self.nballs, :]

        if self.outputdir is not None:
            np.savez_compressed(Path(self.outputdir, 'ballpos.npz'), ballpos=self.ballpos)
            print(f'ballpos.npz saved to {self.outputdir}')

    def track_start_intermediate(self):
        """ Track the first intermediate steps that follows the initialization"""
        for i in range(self.intsteps):
            pos, vel, force = blt.integrate_motion(self, self.surface, return_copies=True)
            self.ballpos_inter[..., i] = pos
            self.vel_inter[..., i] = vel
            self.force_inter.append(force)

    def populate_emergence(self):
        """Detect new emerging features crossing the emergence threshold and place new balls on them"""

        flux_posx, flux_posy = get_local_extrema(self.image, self.polarity, self.ballspacing,
                                                 self.mag_thresh, local_min=self.local_min)

        # TODO: Consider profiling this for optimization
        # Consider getting a view by using tuples of indices...
        # For now we are getting a copy
        # TODO: Consider being consistent with a coarser grid for stronger pixels, in set_bad_balls

        ball_posx, ball_posy = self.pos[0:2, self.new_valid_balls_mask]

        distance_matrix = np.sqrt((flux_posx[:, np.newaxis] - ball_posx[np.newaxis, :])**2 + (flux_posy[:,np.newaxis] - ball_posy[np.newaxis,:])**2)
        distance_min = distance_matrix.min(axis=1)
        populate_flux_mask = distance_min > self.ballspacing + 1

        # Populate only if there's something
        if populate_flux_mask.sum() > 0:

            newposx = flux_posx[populate_flux_mask].view('int32').copy(order='C')
            newposy = flux_posy[populate_flux_mask].view('int32').copy(order='C')

            within_edges_mask = np.logical_not(blt.get_off_edges_mask(self.rs, self.nx, self.ny, newposx, newposy))
            newposx = newposx[within_edges_mask]
            newposy = newposy[within_edges_mask]

            # Emergence detection is pixel-wise. Using interpolation in Matlab was an oversight.
            # only integer coordinates that come out of this. Interpolation is totally useless
            # I can index directly in the array.
            #newposz = self.surface[newposy, newposx]
            newposz = blt.put_balls_on_surface(self.surface, newposx, newposy, self.rs, self.dp)

            # Insert the new positions contiguously in the self.pos array
            # We need to use the number of balls at initialization (self.nballs) and increment it with the number
            # of new balls that will populate and track the emerging flux.
            # newpos = np.array([newposx, newposy, newposz])
            # self.pos = np.concatenate([self.pos, newpos], axis=1)
            self.pos[0, self.nballs:self.nballs + newposx.size] = newposx
            self.pos[1, self.nballs:self.nballs + newposx.size] = newposy
            self.pos[2, self.nballs:self.nballs + newposx.size] = newposz
            # Initialize the velocity, otherwise they could be NaN
            # vel_zero = np.zeros(newpos.shape)
            # self.vel = np.concatenate([self.vel, vel_zero], axis=1)
            self.vel[:, self.nballs:self.nballs + newposx.size] = 0

            # Must add these new balls to self.new_valid_balls_mask and bad_balls_mask
            # new_bad_balls = np.full(newpos.shape[1], False)
            # self.bad_balls_mask = np.concatenate([self.bad_balls_mask, new_bad_balls])
            self.bad_balls_mask[self.nballs:self.nballs + newposx.size] = False
            self.new_valid_balls_mask = np.logical_not(self.bad_balls_mask)
            self.nballs += newposx.size

    def set_bad_balls(self, check_polarity=True, check_noise=True, check_sinking=True):
        """ Flag balls as bad and mask them out based on a set of invalidation criteria
            TODO: this may need to be set as a class method, instead of static
        Args:
            check_polarity (bool): True will flag a bad ball when changing polarity
            check_noise (bool): True will flag a bad ball when falling below noise level
            check_sinking (bool): True will flag a ball when falling below minimum depth

        Returns:

        """
        # See discussion at https://stackoverflow.com/questions/44802033/efficiently-index-2d-numpy-array-using-two-1d-arrays
        # and https://stackoverflow.com/questions/36863404/accumulate-constant-value-in-numpy-array
        # xpos0, ypos0, zpos0 = bt.pos

        # Bad balls are flagged with -1 in the pos array. They will be excluded from the comparison below:

        # It is important to first get rid of the off-edge ones so we can use direct coordinate look-up instead of
        # interpolating the values, which would be troublesome with off-edge coordinates.
        off_edges_mask = blt.get_off_edges_mask(self.rs, self.nx, self.ny, self.pos[0, :], self.pos[1, :])
        # Ignore these bad balls in the arrays and enforce continuity principle
        valid_balls_mask = np.logical_not(off_edges_mask)
        valid_balls_idx = np.nonzero(valid_balls_mask)[0]
        pos2 = self.pos[:, valid_balls_idx].astype(np.int32)
        # Initialize new mask for checking for noise tracking, polarity crossing and sinking balls
        valid_balls_mask2 = np.ones([valid_balls_idx.size], dtype=bool)
        # Forbid crossing flux of opposite polarity and tracking below noise level.

        # The block below checks for polarity crossing, balls tracking in the noise, and sinking balls.
        # It must happens first because the coarse-grid-based decimation that comes next is more expensive
        # with more balls that have no point being in there anyway.
        if check_polarity:
            same_polarity_mask = np.sign(self.image[pos2[1, :], pos2[0, :]]) * self.polarity >= 0
            valid_balls_mask2 = np.logical_and(valid_balls_mask2, same_polarity_mask)
            if not any(valid_balls_mask2.ravel()):
                print('All the balls crossed to opposite polarity. Tracking interrupted.')
                sys.exit(1)

        if check_noise:
            # Track only above noise level. Balls below that noise level are considered "sinking".
            # Use absolute values to make it independent of the polarity that's being tracked.
            noise_mask = np.abs(self.image[pos2[1, :], pos2[0, :]]) > self.noise_level
            valid_balls_mask2 = np.logical_and(valid_balls_mask2, noise_mask)
            if not any(valid_balls_mask2.ravel()):
                print('All the balls are sitting on noise. Tracking interrupted.')
                sys.exit(1)

        if check_sinking:
            # This assumes the vertical position have already been set
            # That is not the case when decimating the balls at the initialization state
            # Thus this check should be set to false during initialization
            unsunk_mask = pos2[2, :] > self.surface[pos2[1, :], pos2[0, :]] - self.min_ds_
            valid_balls_mask2 = np.logical_and(valid_balls_mask2, unsunk_mask)
            if not any(valid_balls_mask2.ravel()):
                print('All the balls have sunk below maximum allowed depth. Tracking interrupted.')
                sys.exit(1)

        # Get indices in the original array. Remember that valid_balls_mask2 has the same size as pos2
        valid_balls_idx = valid_balls_idx[valid_balls_mask2]
        # Get the valid balls from the input pos array.
        # indexing scheme below returns a copy, just like with boolean index arrays.
        xpos, ypos = self.pos[0:2, valid_balls_idx]
        balls_age = self.balls_age[valid_balls_idx]

        ## Decimation based on the coarse grid.
        # Get the 1D position on the coarse grid, clipped to the edges of that grid.
        _, _, coarse_pos = coarse_grid_pos(self, xpos, ypos)

        # Get ball number and balls age sorted by position, sort positions too, and array of valid balls indices as well!!!
        sorted_balls = np.argsort(coarse_pos)
        balls_age = balls_age[sorted_balls]
        coarse_pos = coarse_pos[sorted_balls]
        valid_balls_idx = valid_balls_idx[sorted_balls]
        # There can be repetitions in the coarse_pos because there can be more than one ball per finegrid cell.
        # The point is to keep only one ball per coarse grid point: the oldest.
        # So we need to sort coarse_pos further using the balls age as weight and extract a unique set where each ball is the oldest
        sidx = np.lexsort([balls_age, coarse_pos])
        # Indices of the valid balls to keep
        self.unique_valid_balls = valid_balls_idx[sidx[np.r_[np.flatnonzero(coarse_pos[1:] != coarse_pos[:-1]), -1]]]

        # Now the point is to have a mask or list of balls at overpopulated cells.
        # They are simply the ones not listed by unique_oldest_balls
        self.bad_balls_mask = np.ones([self.pos.shape[1]], dtype=bool)
        self.bad_balls_mask[self.unique_valid_balls] = False
        # Update mask of valid balls and increment the age of valid balls only
        self.new_valid_balls_mask = np.logical_not(self.bad_balls_mask)
        return

    def export_track_figures(self, axlims=None, **kwargs):
        """ Export figures with the balls centers overlaying the original images

        Args:
            axlims (): axis limits for the figure generated by imshow()
            **kwargs (): pass optional arguments to imshow() in plot_balls_over_frame

        Returns:

        """
        for n in range(0, self.nt):
            image = load_data(self.datafiles, n, astropy=self.astropy, roi=self.roi)
            fig_title = Path(self.fig_dir, f'track_figures_{n:04d}.png')
            plot_balls_over_frame(image, self.ballpos[0, :, n], self.ballpos[1, :, n], fig_title, axlims=axlims, **kwargs)


def mballtrack_main_positive(**kwargs):
    """ Main function for Magnetic Balltracking for tracking positive polarity"""
    mbt_p = MBT(polarity=1, **kwargs)
    mbt_p.track_all_frames()

    return mbt_p


def mballtrack_main_negative(**kwargs):
    """ Main function for Magnetic Balltracking for tracking negatie polarity"""
    mbt_n = MBT(polarity=-1, **kwargs)
    mbt_n.track_all_frames()

    return mbt_n


def mballtrack_main(**kwargs):
    """ Main function for Magnetic Balltracking for tracking both polarities independently"""
    mbt_p = mballtrack_main_positive(**kwargs)
    mbt_n = mballtrack_main_negative(**kwargs)

    return mbt_p, mbt_n


def load_data(datafiles, n, astropy=False, roi=None):
    """Load the list of input images given as FITS files

    Args:
        datafiles (list): list of paths to the images in FITS files
        n (int): frame or slice index to load
        astropy (bool): True will use the Astropy package to handle FITS files. False will use package 'fitsio' package
        roi (tuple): Region of Interest to extract (ymin, ymax, xmin, xmax)

    Returns:
        image (ndarray): 2D numpy array
    """
    _, ext = os.path.splitext(datafiles[0])
    if ext in ('npz', 'npy'):
        image = load_npz(datafiles, n)
        if roi is not None:
            image = image[roi]
        return image
    else:
        # If the file is a fits cube, will read only one slice without reading the whole cube in memory
        # does not work with astropy.io.fits, only with fitsio
        image = fitstools.fitsread(datafiles, tslice=n).astype(DTYPE)
        if roi is not None:
            image = image[roi]
        return image


def load_npz(datafiles, n):
    """Load input files as Numpy save files"""

    data = np.load(datafiles[n])
    image = data[data.files[0]]
    return image


def get_local_extrema(image, polarity, min_distance, threshold, local_min=False, xlims=None, ylims=None):
    """ Default to finding only local maxima. local_min = True will look only for local minima

    Args:
        image (ndarray): 2D frame displaying the features to track.
        polarity (bool): if data are signed (e.g. magnetograms), set which polarity is tracked >= 0 for positive polarity
        min_distance (int): minimum distance to search between local extrema
        threshold (float or tuple): values setting the limit for searching for local extrema. Can be a signed value or min & max
        local_min (int): if True, will look for local minima instead of local maxima
        xlims (tuple): minimum and maximum pixel x-coordinate to look for peaks
        ylims (tuple): minimum and maximum pixel y-coordinate to look for peaks

    Returns:
        xstart: list of x-coordinates of the local extrema
        ystart: list of y-coordinates of the local extrema
    """

    # Get a mask of where to look for local maxima.
    if isinstance(threshold, int) or isinstance(threshold, float):
        if polarity >= 0:
            mask_thresh = image >= threshold
        else:
            mask_thresh = image <= -threshold
    else:  # Useful for active regions
        mask_thresh = (image > min(threshold)) & (image < max(threshold))
    if xlims is not None:
        mask_thresh[:, 0:xlims[0]] = False
        mask_thresh[:, xlims[1]:] = False
    if ylims is not None:
        mask_thresh[0:ylims[0], :] = False
        mask_thresh[ylims[1]:, :] = False

    if local_min:
        # reverse the scale of the image so the local min are searched as local max
        image2 = image.max() - image
        ystart, xstart = peak_local_max(np.abs(image2), min_distance=min_distance, labels=mask_thresh).T
    else:
        #se = disk(round(min_distance/2))
        #ystart, xstart = np.array( peak_local_max(np.abs(image), indices=True, footprint=se,labels=mask_maxi)).T
        ystart, xstart = peak_local_max(np.abs(image), min_distance=min_distance, labels=mask_thresh).T

    # Because transpose only creates a view, and this is eventually given to a C function,
    # it needs to be copied as C-ordered
    # TODO: revisit this...
    return xstart.copy(order='C'), ystart.copy(order='C')


def get_local_extrema_ar(image, polarity, min_distance, threshold, threshold2, local_min=False):
    """ Find the coordinates of local extrema with special treatment of Active Regions.

    Similar to get_local_extrema() but uses a grid size (min_distance) 3x greater
    in regions that exceeds a higher threshold.

    Args:
        image: typically a magnetogram. Can be anything whose larger region have a pixels of higher intensity.
        polarity: 0,+1 for positive flux or intensity. -1 for negative flux or intensity
        min_distance: minimum distance to consider between local extrema.
        threshold: pixels below this value are ignored
        threshold2: values that define the regions of higher intensity.
        local_min (int): if True, will look for local minima instead of local maxima

    Returns:
        xstart: x-coordinates of local extrema
        ystart: y-coordinates of local extrema
    """

    xstart, ystart = get_local_extrema(image, polarity, min_distance, threshold, local_min=local_min)
    # Get the intensity at these locations
    data_int = image[ystart, xstart]
    # Build a distance-based matrix for coordinates of pixel whose intensity is above threshold2, and keep the maximum
    if polarity >= 0:
        select_mask = np.logical_and(data_int >=0, data_int < threshold2)
        mask_maxi_sunspots = image >= threshold2
    else:
        select_mask = np.logical_and(data_int < 0, data_int > -threshold2)
        mask_maxi_sunspots = image < - threshold2

    xstart1, ystart1 = xstart[select_mask], ystart[select_mask]

    se = disk(round(min_distance/2))
    #se = np.ones([3*min_distance, 3*min_distance])

    # ystart2, xstart2 = np.array(peak_local_max(np.abs(image), indices=True,
    #                                            footprint= se,
    #                                            labels=mask_maxi_sunspots), dtype=DTYPE).T

    ystart2, xstart2 = peak_local_max(np.abs(image), indices=True,
                                               min_distance=min_distance,
                                               labels=mask_maxi_sunspots).T

    xstart = np.concatenate((xstart1, xstart2))
    ystart = np.concatenate((ystart1, ystart2))

    return xstart, ystart

# def get_local_extrema_ar2(image, polarity, threshold):
#
#
#     data = image.astype(np.float64)
#     #TODO: Check if this is not overkill; since each value is compared against the threshold, this might not be needed
#     if polarity >= 0:
#         signed_data = np.ma.masked_less(data, 0).filled(0)
#     else:
#         signed_data = np.ma.masked_less(-data, 0).filled(0)
#
#     labels = segmentation.detect_polarity(signed_data, float(threshold))
#
#     return labels


def prep_data(image):
    """ Default image prepping for Magnetic Balltracking

    Args:
        image (ndarray): 2D array of the image

    Returns:
        surface (ndarray): 2D array of the rescaled image

    """
    image2 = np.sqrt(np.abs(image))
    image3 = image2.max() - image2
    surface = (image3 - image3.mean())/image3.std()
    return surface.copy(order='C').astype(DTYPE)


def coarse_grid_pos(mbt, x, y):

    # Get the position on the coarse grid, clipped to the edges of that grid.
    xcoarse = np.uint32(np.clip(np.floor(x / mbt.ballspacing), 0, mbt.nxc_ - 1))
    ycoarse = np.uint32(np.clip(np.floor(y / mbt.ballspacing), 0, mbt.nyc_ - 1))
    # Convert to linear (1D) indices. One index per ball
    #coarse_idx = np.ravel_multi_index((ycoarse, xcoarse), mbt.coarse_grid.shape)
    coarse_idx = np.ravel_multi_index((ycoarse, xcoarse), (mbt.nyc_, mbt.nxc_))
    return xcoarse, ycoarse, coarse_idx


def merge_positive_negative_tracking(mbt_p, mbt_n):

    # Get a view that gets rid of the z-coordinate
    pos_p = mbt_p.ballpos[slice(0,1), ...]
    pos_n = mbt_n.ballpos[slice(0,1), ...]
    # Merge
    pos = np.concatenate((pos_p, pos_n), axis=1)
    return pos


def get_balls_at(x, y, xpos, ypos, tolerance=0.2):
    """Get balls near a certain position"""
    return np.where((np.abs(xpos - x) < tolerance) & (np.abs(ypos - y) < tolerance))[0]


def label_from_pos(x, y, dims):
    """Create a multi-label set for the marker-based watershed algorithm"""
    label_map = np.zeros(dims, dtype=np.int32)
    labels = np.arange(x.size, dtype=np.int32)+1
    # This assumes bad balls are flagged with coordinate value of -1 in x (and y)
    valid_mask = x > 0
    label_map[y[valid_mask], x[valid_mask]] = labels[valid_mask]

    return label_map


def marker_watershed(data, x, y, threshold, polarity, invert=True):
    """Marker-based watershed algorithms

    E.g. use the balls x & y positions as markers

    Args:
        data (ndarray): image array
        x (ndarray): x-coordinates of the markers
        y (ndarray): y-coordinates of the markers
        threshold (int): mask out data beyond threshold
        polarity (bool): determines which side of the threshold we consider
        invert (bool): Invert the data, necessary with magnetograms.

    Returns:
        labels: multi-label array
        markers: marker labels used by the watershed algorithm
        borders: border data
    """
    markers = label_from_pos(x, y, data.shape)
    if polarity >= 0:
        mask_ws = data > threshold
    else:
        mask_ws = data < -threshold

    wdata = np.abs(data)
    # For magnetograms, need to invert the absolute value so the fragment intensity decreases toward centroid
    if invert:
        wdata -= wdata

    labels = watershed(wdata, markers, mask=mask_ws)
    borders = find_boundaries(labels)
    # Subtract 1 to align with the ball number series. E.g: watershed label 0 corresponds to ball #0
    labels -= 1
    return labels, markers, borders


def watershed_series(datafile, nframes, threshold, polarity, ballpos, verbose=False, prep_function=None, invert=True,
                     astropy=False, roi=None):
    """Applies the watershed algorithm to a series of images"""

    # Load a sample to determine shape
    #data = fitstools.fitsread(datafile, tslice=0)
    data = load_data(datafile, 0, astropy=astropy, roi=roi)
    if prep_function is not None:
        data = prep_function(data)

    ws_series = np.empty([nframes, data.shape[1], data.shape[0]], dtype=np.int32)
    markers_series = np.empty([nframes, data.shape[1], data.shape[0]], dtype=np.int32)
    borders_series = np.empty([nframes, data.shape[1], data.shape[0]], dtype=np.bool)

    # For parallelization, need to see how to share a proper container, whatever is more efficient
    for n in range(nframes):
        if verbose:
            print('Watershed series frame n = %d'%n)
        #data = fitstools.fitsread(datafile, tslice=n)
        data = load_data(datafile, n, astropy=astropy, roi=roi)
        # Get a view of (x,y) coords at frame #i (use slice instead of fancy indexing). Either with slice(0,1) or 0:2
        # I'll use slice for clarity
        # positions = ballpos[slice(0,1),:,n]
        labels_ws, markers, borders = marker_watershed(data, ballpos[0,:,n], ballpos[1,:,n], threshold, polarity, invert=invert)
        ws_series[n,...] = labels_ws
        markers_series[n, ...] = markers
        borders_series[n, ...] = borders

    return ws_series, markers_series, borders_series


def merge_watershed(labels_p, borders_p, nballs_p, labels_n, borders_n):
    """
    Merge the results from markers-watershed the positive and negative flux.
    The output borders array for positive flux stay at +1, but borders of negative values are set at -1.

    Args:
        labels_p: Array of watershed labels for positive flux
        borders_p: Array of watershed borders for positive flux
        labels_n: Array of watershed labels for negative flux
        borders_n: Array of watershed borders for negative flux

    Returns:
        ws_labels: multi-label array of same shape as input.
        borders: Array containing the borders. +1 on borders of positive flux, -1 for negative flux
    """

    ws_labels = labels_p.copy()
    ws_labels[labels_n >= 0] = nballs_p + labels_n[labels_n >= 0] + 1
    borders = borders_p.copy().astype(np.int8)
    borders[borders_n == 1] = -1

    return ws_labels, borders


def plot_balls_over_frame(frame, ballpos_x, ballpos_y, fig_path, z=None, figsize=None, axlims=None,
                          title=None, ms=4, ballvel=None, **kwargs):

    plt.figure(0, figsize=figsize)
    plt.imshow(frame, origin='lower', **kwargs)
    plt.plot(ballpos_x, ballpos_y, 'ro', markerfacecolor='None', ms=2*ms)
    nbadballs = np.count_nonzero(ballpos_x == -1)
    print('nb of bad balls = ', nbadballs)
    if title is not None:
        title = title + f'    # of bad balls = {nbadballs}'
    else:
        title = f' # of bad balls = {nbadballs}'

    plt.title(title)
    if axlims is not None:
        plt.axis(axlims)
    plt.xlabel('X [px]')
    plt.ylabel('Y [px]')
    plt.colorbar()

    for b in range(ballpos_x.size):
        x = ballpos_x[b]
        y = ballpos_y[b]

        if x > 0 and y > 0:
            if z is not None:
                plt.text(x+2, y+2, f'z={z[b]:2.0f}', color='red', clip_on=True, fontsize=5)
            if ballvel is not None:

                vx = ballvel[0, b]
                if vx >= 0:
                    color = 'purple'
                else:
                    color = 'cyan'

                plt.plot(x, y, marker='o', markeredgecolor=color, markerfacecolor='None', ms=2 * ms)
                plt.quiver(x, y, vx, 0,
                           color=color, angles='xy', scale_units='xy', scale=0.2,
                           width=0.004,
                           headwidth=2, headlength=2, headaxislength=2)


    plt.tight_layout()
    plt.savefig(fig_path, dpi=180)
    plt.close()


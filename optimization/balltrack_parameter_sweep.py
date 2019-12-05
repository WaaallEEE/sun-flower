import os, sys
import numpy as np
import balltracking.balltrack as blt
import multiprocessing
from multiprocessing import Pool
from functools import partial
import time
import pandas as pd
from collections import OrderedDict


if __name__  == '__main__':


    # the multiprocessing start method can only bet set once.
    multiprocessing.set_start_method('spawn')
    nprocesses = 4
    # directory hosting the drifted data (9 series)
    drift_dir = os.path.expanduser('~/Data/sanity_check/stein_series/')
    # output directory for the drifting images
    outputdir = os.path.join(drift_dir, 'calibration')
    reprocess_bt = True
    nframes = 30
    trange = [0, nframes]

    # Ball parameters
    bt_params = OrderedDict({'rs': 2})
    # Parameter sweep
    intsteps = [3,4,5,6]
    ballspacing = [1, 2, 3]
    dp_l = [0.2, 0.25, 0.3, 0.35, 0.4]
    sigma_factor_l = [1.25, 1.5, 1.75, 2]
    # Fourier filter radius
    f_radius_l = np.arange(0, 16)
    bt_params_list = blt.get_bt_params_list(bt_params,
                                            ('intsteps', 'ballspacing', 'dp', 'sigma_factor', 'fourier_radius'),
                                            (intsteps, ballspacing, dp_l, sigma_factor_l, f_radius_l))
    ### Velocity smoothing
    fwhm = 7
    kernel = 'boxcar'
    ##########################################
    ## Calibration parameters
    # Set npts drift rates
    dv = 0.04
    vx_rates = np.arange(-0.2, 0.21, dv)
    vx_rates[int(len(vx_rates) / 2)] = 0
    vx_labels = ['vx_{:02d}'.format(i) for i in range(len(vx_rates))]

    drift_rates = np.stack((vx_rates, np.zeros(len(vx_rates))), axis=1).tolist()
    imsize = 263  # actual size is 264 but original size was 263 then padded to 264 to make it even for the Fourier transform
    trim = int(vx_rates.max() * nframes + fwhm + 2)
    fov_slices = np.s_[trim:imsize - trim, trim:imsize - trim]

    dims = [imsize, imsize]

    # Prepare partial function for parallel pool & map.
    calibrate_partial = partial(blt.balltrack_calibration,
                                drift_rates=drift_rates,
                                trange=trange,
                                fov_slices=fov_slices,
                                reprocess_bt=reprocess_bt,
                                drift_dir=drift_dir,
                                outputdir=outputdir,
                                kernel=kernel,
                                fwhm=fwhm,
                                dims=dims,
                                basename='im_shifted',
                                write_ballpos_list=False,
                                nthreads=4)


    df = calibrate_partial(bt_params_list[2913])

    #map(calibrate_partial, bt_params_list[0:6])

    # pool = Pool(processes=nprocesses)
    # pool.map(calibrate_partial, bt_params_list[0:12])
    # pool.close()
    # pool.join()




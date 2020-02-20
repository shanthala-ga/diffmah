"""
"""
import numpy as np
from math import exp, log, log10
from numba import njit
from .halo_vpeak_evolution import vmax_vs_mhalo_and_redshift
from .sigmoid_mah import median_logmpeak_from_logt

__all__ = ("main_sequence_sfr_median_halo_growth",)


def main_sequence_sfr_median_halo_growth(logmpeak_at_z0, redshift, cosmic_time):
    """Model of star formation history of Main Sequence galaxies,
    assuming median dark matter halo growth.

    Parameters
    ----------
    logmpeak_at_z0 : float or ndarray of shape (nhalos, )
        Halo mass at redshift zero in units of Msun/h

    redshift : float or ndarray of shape (nhalos, )

    cosmic_time : float or ndarray of shape (nhalos, )
        Age of the universe in Gyr

    Returns
    -------
    sfr : ndarray of shape (nhalos, )
        Star formation rate in units of Msun/yr

    """
    logmah = median_logmpeak_from_logt(np.log10(cosmic_time), logmpeak_at_z0)
    vmaxh = vmax_vs_mhalo_and_redshift(10 ** logmah, redshift)
    return mean_sfr_vs_vmax_redshift_bestfit_um(vmaxh, redshift)


def main_sequence_sfr_vs_mpeak_and_redshift(mpeak, redshift):
    """Model of star formation history of Main Sequence galaxies.

    Parameters
    ----------
    mpeak : float or ndarray of shape (nhalos, )
        Halo mass at the input redshift in units of Msun/h

    redshift : float or ndarray of shape (nhalos, )
        Redshift at which the halo has mass mpeak

    Returns
    -------
    sfr : ndarray of shape (nhalos, )
        Star formation rate in units of Msun/yr

    """
    mpeak, redshift = _get_1d_arrays(mpeak, redshift)
    vmax = vmax_vs_mhalo_and_redshift(mpeak, redshift)
    result = np.zeros(vmax.size).astype("f4")
    _mean_sfr_vs_vmax_redshift(vmax, redshift, result)
    return result


def mean_sfr_vs_vmax_redshift_bestfit_um(vmax, redshift):
    """Model of star formation history of Main Sequence galaxies.

    See https://arxiv.org/abs/1806.07893, Eqs. 4-11.

    Parameters
    ----------
    vmax : float or ndarray of shape (nhalos, )
        Maximum circular velocity of the halo
        at the input redshift in units of km/s

    redshift : float or ndarray of shape (nhalos, )
        Redshift at which the halo has circular velocity vmax

    Returns
    -------
    sfr : ndarray of shape (nhalos, )
        Star formation rate in units of Msun/yr

    """
    vmax, redshift = _get_1d_arrays(vmax, redshift)
    result = np.zeros(vmax.size).astype("f4")
    _mean_sfr_vs_vmax_redshift(vmax, redshift, result)
    return result


@njit
def _mean_sfr_vs_vmax_redshift(
    vmax,
    redshift,
    result,
    logV_0=2.151,
    logV_a=-1.658,
    logV_lnz=1.68,
    logV_z=-0.233,
    alpha_0=-5.598,
    alpha_a=-20.731,
    alpha_lnz=13.455,
    alpha_z=-1.321,
    beta_0=-1.911,
    beta_a=0.395,
    beta_z=0.747,
    gamma_0=-1.699,
    gamma_a=4.206,
    gamma_z=-0.809,
    delta_0=0.055,
    epsilon_0=0.109,
    epsilon_a=-3.441,
    epsilon_lnz=5.079,
    epsilon_z=-0.781,
):
    """
    """
    n = result.size
    for i in range(n):
        z = redshift[i]
        a = 1 / (1 + z)

        V = 10 ** (logV_0 + logV_a * (1 - a) + logV_lnz * log(1 + z) + logV_z * z)
        v = vmax[i] / V

        _a = alpha_0 + alpha_a * (1 - a) + alpha_lnz * log(1 + z) + alpha_z * z
        _b = beta_0 + beta_a * (1 - a) + beta_z * z
        term1 = 1 / (v ** _a + v ** _b)

        _log10v = log10(v)
        exp_arg = (-_log10v * _log10v) / (2 * delta_0)

        _logGamma = gamma_0 + gamma_a * (1 - a) + gamma_z * z
        term2 = (10 ** _logGamma) * exp(exp_arg)

        log10_epsilon = (
            epsilon_0 + epsilon_a * (1.0 - a) + epsilon_lnz * log(1 + z) + epsilon_z * z
        )

        result[i] = (10 ** log10_epsilon) * (term1 + term2)


def _get_1d_arrays(*args):
    """Return a list of ndarrays of the same length.

    Each arg must be either an ndarray of shape (npts, ), or a scalar.

    """
    results = [np.atleast_1d(arg) for arg in args]
    sizes = [arr.size for arr in results]
    npts = max(sizes)
    msg = "All input arguments should be either a float or ndarray of shape ({0}, )"
    assert set(sizes) <= set((1, npts)), msg.format(npts)
    return [np.zeros(npts).astype(arr.dtype) + arr for arr in results]

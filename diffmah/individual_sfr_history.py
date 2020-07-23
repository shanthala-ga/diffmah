"""Module implements the individual_log_sfr_history function."""
import numpy as np
from collections import OrderedDict
from jax import numpy as jax_np
from jax import jit as jax_jit
from .main_sequence_sfr_eff import _log_sfr_efficiency_ms_jax_kern
from .halo_assembly import _individual_halo_assembly_jax_kern
from .halo_assembly import _process_halo_mah_args, individual_halo_assembly_history
from .halo_assembly import DEFAULT_MAH_PARAMS, TODAY
from .main_sequence_sfr_eff import DEFAULT_SFR_MS_PARAMS
from .quenching_times import _jax_gradual_quenching


FB = 0.158

QUENCHING_DICT = OrderedDict(log_qtime=0.9, qspeed=5)
DEFAULT_SFRH_PARAMS = OrderedDict()
DEFAULT_SFRH_PARAMS.update(DEFAULT_SFR_MS_PARAMS)
DEFAULT_SFRH_PARAMS.update(QUENCHING_DICT)

COSMIC_TIME_TABLE = np.linspace(0.1, 14, 500)


def predict_in_situ_history_collection(
    mah_params,
    sfr_params,
    cosmic_time,
    t_table=COSMIC_TIME_TABLE,
    fstar_timescales=(),
    log_ssfr_clip=-11,
):
    """
    Predict histories of SM, sSFR, and Fstar for a collection of halos.

    Parameters
    ----------
    mah_params : ndarray of shape (nhalos, 6)
        In order, stores tmp, logmp, dmhdt_x0, dmhdt_k,
        dmhdt_early_index, dmhdt_late_index for every halo

    sfr_params : ndarray of shape (nhalos, n_sfr_params)
        The sfr params and their ordering are consistent with DEFAULT_SFRH_PARAMS

    cosmic_time : ndarray of shape (n, )
        Age of the universe in Gyr at which to evaluate the assembly history.

        The size n should be large enough so that the log_sm integration
        can be accurately calculated with the midpoint rule.
        Typically n >~100 is sufficient for most purposes.

    fstar_timescales : float or sequence, optional
        Smoothing timescale(s) tau over which to compute Fstar,
        the fraction of stellar mass formed between (t - tau, t).

    log_ssfr_clip : float, optional
        Minimum value of sSFR. Default is -11

    Returns
    -------
    result : list
        Each element of result is an ndarray of shape (nhalos, ntimes).
        First element of result is log_mah, cumulative halo mass.
        Second element of result is log_sm, cumulative in-situ stellar mass.
        Third element of result is log_ssfrh, specific SFR history,
        clipped at the input log_ssfr_clip.
        Optional remaining elements store fstarh, the fraction of stellar mass
        formed between (t - tau, t), for each tau in the input fstar_timescales.

    """
    nhalos, n_mah_params = mah_params.shape
    _nhalos, n_sfr_params = sfr_params.shape
    assert nhalos == _nhalos, "mismatched shapes for mah_params and sfr_params"

    nt = len(cosmic_time)
    log_mah = np.zeros((nhalos, nt)).astype("f4")
    log_smh = np.zeros((nhalos, nt)).astype("f4")
    log_ssfrh = np.zeros((nhalos, nt)).astype("f4")

    try:
        n_fstar = len(fstar_timescales)
    except TypeError:
        assert fstar_timescales > 0, "Input fstar_timescales must be strictly positive"
        fstar_timescales = (fstar_timescales,)
        n_fstar = len(fstar_timescales)

    fstar_coll = [np.zeros((nhalos, nt)).astype("f4") for __ in range(n_fstar)]

    mah_names = list(DEFAULT_MAH_PARAMS.keys())
    sfr_names = list(DEFAULT_SFRH_PARAMS.keys())
    for ihalo in range(nhalos):
        tmp = mah_params[ihalo, 0]
        logmp = mah_params[ihalo, 1]
        mah_pgen = zip(mah_names, mah_params[ihalo, 2:])
        sfr_pgen = zip(sfr_names, sfr_params[ihalo, :])
        mah_dict = OrderedDict([(key, val) for key, val in mah_pgen])
        sfr_dict = OrderedDict([(key, val) for key, val in sfr_pgen])
        _x_table = predict_in_situ_history(
            t_table,
            logmp,
            fstar_timescales=fstar_timescales,
            **mah_dict,
            **sfr_dict,
            tmp=tmp,
            log_ssfr_clip=log_ssfr_clip
        )
        log_mah[ihalo, :] = np.interp(
            np.log10(cosmic_time), np.log10(t_table), _x_table[0]
        )
        log_smh[ihalo, :] = np.interp(
            np.log10(cosmic_time), np.log10(t_table), _x_table[1]
        )
        log_ssfrh[ihalo, :] = np.interp(
            np.log10(cosmic_time), np.log10(t_table), _x_table[2]
        )
        for it in range(len(fstar_timescales)):
            fstar_coll[it][ihalo, :] = np.interp(
                np.log10(cosmic_time), np.log10(t_table), _x_table[3][it]
            )

    return [log_mah, log_smh, log_ssfrh, *fstar_coll]


def predict_in_situ_history(
    cosmic_time,
    logmp,
    fstar_timescales=(),
    dmhdt_x0=DEFAULT_MAH_PARAMS["dmhdt_x0"],
    dmhdt_k=DEFAULT_MAH_PARAMS["dmhdt_k"],
    dmhdt_early_index=DEFAULT_MAH_PARAMS["dmhdt_early_index"],
    dmhdt_late_index=DEFAULT_MAH_PARAMS["dmhdt_late_index"],
    lge0=DEFAULT_SFRH_PARAMS["lge0"],
    k_early=DEFAULT_SFRH_PARAMS["k_early"],
    lgtc=DEFAULT_SFRH_PARAMS["lgtc"],
    lgec=DEFAULT_SFRH_PARAMS["lgec"],
    k_trans=DEFAULT_SFRH_PARAMS["k_trans"],
    a_late=DEFAULT_SFRH_PARAMS["a_late"],
    log_qtime=DEFAULT_SFRH_PARAMS["log_qtime"],
    qspeed=DEFAULT_SFRH_PARAMS["qspeed"],
    tmp=TODAY,
    log_ssfr_clip=-11,
):
    """
    Predict histories of SM, sSFR, and Fstar for an individual halo.

    Parameters
    ----------
    cosmic_time : ndarray of shape (n, )
        Age of the universe in Gyr at which to evaluate the assembly history.

        The size n should be large enough so that the log_sm integration
        can be accurately calculated with the midpoint rule.
        Typically n >~100 is sufficient for most purposes.

    logmp : float
        Base-10 log of peak halo mass in units of Msun

    fstar_timescales : float or sequence, optional
        Smoothing timescale(s) tau over which to compute Fstar,
        the fraction of stellar mass formed between (t - tau, t).

    **kwargs : float, optional
        Any individual MAH parameter or SFR parameter is accepted
        Defaults are set by DEFAULT_MAH_PARAMS and DEFAULT_SFRH_PARAMS.

    tmp : float, optional
        Age of the universe in Gyr at the time halo mass attains the input logmp.
        There must exist some entry of the input cosmic_time array within 50Myr of tmp.
        Default is ~13.85 Gyr.

    log_ssfr_clip : float, optional
        Minimum value of sSFR. Default is -11

    Returns
    -------
    log_sm : ndarray of shape (n, )
        Stores cumulative in-situ stellar mass.

    log_ssfr : ndarray of shape (n, )
        Stores specific star-formation history

    fstar_collector : list, optional
        Only returned if fstar_timescales is not None

    """
    log_mah, log_dmhdt = individual_halo_assembly_history(
        cosmic_time,
        logmp,
        tmp=tmp,
        dmhdt_x0=dmhdt_x0,
        dmhdt_k=dmhdt_k,
        dmhdt_early_index=dmhdt_early_index,
        dmhdt_late_index=dmhdt_late_index,
    )

    log_sfr, log_sm = individual_sfr_history(
        cosmic_time,
        logmp,
        dmhdt_x0,
        dmhdt_k,
        dmhdt_early_index,
        dmhdt_late_index,
        lge0,
        k_early,
        lgtc,
        lgec,
        k_trans,
        a_late,
        log_qtime,
        qspeed,
        tmp,
    )
    log_ssfr = log_sfr - log_sm
    if log_ssfr_clip is not None:
        log_ssfr = np.where(log_ssfr < log_ssfr_clip, log_ssfr_clip, log_ssfr)

    try:
        len(fstar_timescales)
    except TypeError:
        assert fstar_timescales > 0, "Input fstar_timescales must be strictly positive"
        fstar_timescales = (fstar_timescales,)

    fstar_collector = []
    for tau_s in fstar_timescales:
        fs = _compute_fstar(cosmic_time, log_sm, tau_s)
        fstar_collector.append(fs)

    if len(fstar_collector) > 0:
        return log_mah, log_sm, log_ssfr, fstar_collector
    else:
        return log_mah, log_sm, log_ssfr


def _compute_fstar(tarr, in_situ_log_sm, tau_s):
    """Assumes log_sm is monotonic."""
    t_lag = tarr - tau_s
    min_t_lag = tarr[0]
    t_lag = np.where(t_lag < min_t_lag, min_t_lag, t_lag)
    log_sm_at_t_lag = np.interp(np.log10(t_lag), np.log10(tarr), in_situ_log_sm)
    sm_at_t = 10 ** in_situ_log_sm
    sm_at_t_lag = 10 ** log_sm_at_t_lag
    fstar = 1 - sm_at_t_lag / sm_at_t
    return np.where(fstar < 0, 0, fstar)


def individual_sfr_history(
    cosmic_time,
    logmp,
    dmhdt_x0=DEFAULT_MAH_PARAMS["dmhdt_x0"],
    dmhdt_k=DEFAULT_MAH_PARAMS["dmhdt_k"],
    dmhdt_early_index=DEFAULT_MAH_PARAMS["dmhdt_early_index"],
    dmhdt_late_index=DEFAULT_MAH_PARAMS["dmhdt_late_index"],
    lge0=DEFAULT_SFRH_PARAMS["lge0"],
    k_early=DEFAULT_SFRH_PARAMS["k_early"],
    lgtc=DEFAULT_SFRH_PARAMS["lgtc"],
    lgec=DEFAULT_SFRH_PARAMS["lgec"],
    k_trans=DEFAULT_SFRH_PARAMS["k_trans"],
    a_late=DEFAULT_SFRH_PARAMS["a_late"],
    log_qtime=DEFAULT_SFRH_PARAMS["log_qtime"],
    qspeed=DEFAULT_SFRH_PARAMS["qspeed"],
    tmp=TODAY,
):
    """Model for star formation history vs time for a halo with present-day mass logmp.

    Parameters
    ----------
    cosmic_time : ndarray of shape (n, )
        Age of the universe in Gyr at which to evaluate the assembly history.

    logmp : float
        Base-10 log of peak halo mass in units of Msun

    qtime : float, optional
        Quenching time in units of Gyr.
        Default is 14 for negligible quenching before z=0.

    lge0 : float, optional
        Asymptotic value of SFR efficiency at early times.
        Default set according to average value for Milky Way halos.

    lgtc : float, optional
        Time of peak star formation in Gyr.
        Default set according to average value for Milky Way halos.

    lgec : float, optional
        Normalization of SFR efficiency at the time of peak SFR.
        Default set according to average value for Milky Way halos.

    a_late : float, optional
        Late-time power-law index of SFR efficiency.
        Default set according to average value for Milky Way halos.

    Additional MAH parameters:
            dmhdt_x0, dmhdt_k, dmhdt_early_index, dmhdt_late_index

        Unspecified MAH parameters will be set according to the
        median growth history for a halo of mass logmp.

        See halo_assembly.DEFAULT_MAH_PARAMS for more info on MAH parameters
        See main_sequence_sfr_eff.DEFAULT_SFR_MS_PARAMS
        for more info on SFR efficiency  parameters

    tmp : float, optional
        Age of the universe in Gyr at the time halo mass attains the input logmp.
        There must exist some entry of the input cosmic_time array within 50Myr of tmp.
        Default is ~13.85 Gyr.

    Returns
    -------
    log_sfr : ndarray of shape (n, )
        Base-10 log of star formation rate in units of Msun/yr

    """
    logmp, logt, dtarr, indx_tmp = _process_halo_mah_args(logmp, cosmic_time, tmp)

    log_sfr, log_sm = _individual_log_mstar_history_jax_kern(
        logt,
        dtarr,
        logmp,
        dmhdt_x0,
        dmhdt_k,
        dmhdt_early_index,
        dmhdt_late_index,
        lge0,
        k_early,
        lgtc,
        lgec,
        k_trans,
        a_late,
        log_qtime,
        qspeed,
        indx_tmp,
    )
    log_sfr, log_sm = np.array(log_sfr), np.array(log_sm)
    return log_sfr, log_sm


@jax_jit
def _individual_log_sfr_history_jax_kern(
    logt,
    dtarr,
    logmp,
    dmhdt_x0,
    dmhdt_k,
    dmhdt_early_index,
    dmhdt_late_index,
    lge0,
    k_early,
    lgtc,
    lgec,
    k_trans,
    a_late,
    log_qtime,
    qspeed,
    indx_tmp,
):

    log_dmhdt = _individual_halo_assembly_jax_kern(
        logt,
        dtarr,
        logmp,
        dmhdt_x0,
        dmhdt_k,
        dmhdt_early_index,
        dmhdt_late_index,
        indx_tmp,
    )[1]
    log_dmbdt = jax_np.log10(FB) + log_dmhdt

    log_sfr_eff = _log_sfr_efficiency_ms_jax_kern(
        logt, lge0, k_early, lgtc, lgec, k_trans, a_late
    )
    log_sfr_ms = log_dmbdt + log_sfr_eff
    log_sfr = log_sfr_ms + _jax_gradual_quenching(logt, log_qtime, qspeed)

    return log_sfr


@jax_jit
def _calculate_cumulative_in_situ_mass(log_sfr, dtarr):
    log_smh = jax_np.log10(jax_np.cumsum(jax_np.power(10, log_sfr)) * dtarr) + 9.0
    return log_smh


@jax_jit
def _individual_log_mstar_history_jax_kern(
    logt,
    dtarr,
    logmp,
    dmhdt_x0,
    dmhdt_k,
    dmhdt_early_index,
    dmhdt_late_index,
    lge0,
    k_early,
    lgtc,
    lgec,
    k_trans,
    a_late,
    log_qtime,
    qspeed,
    indx_tmp,
):
    log_sfr = _individual_log_sfr_history_jax_kern(
        logt,
        dtarr,
        logmp,
        dmhdt_x0,
        dmhdt_k,
        dmhdt_early_index,
        dmhdt_late_index,
        lge0,
        k_early,
        lgtc,
        lgec,
        k_trans,
        a_late,
        log_qtime,
        qspeed,
        indx_tmp,
    )
    log_smh = _calculate_cumulative_in_situ_mass(log_sfr, dtarr)
    return log_sfr, log_smh

"""Combination policies of a measurement channel (R37).

How ONE measurement channel reduces the several readings arriving on it to
a single number. Pure functions and names: this module drives no engine,
so the declaration grammar can read the policy names without one.
"""

import statistics

# ----------------------------------------------------------------------
# Combination policies (R37) -- how ONE measurement channel reduces the
# several readings arriving on it to a single number
# ----------------------------------------------------------------------
#
# The mirror image of the allocation policies of ``muscadet.flow_continuous``.
# An output declares how it SPLITS one quantity among several consumers; a
# measurement input declares how it COMBINES the several readings its producers
# publish. Same shape, same extension point: a named policy, or a Python
# callable used in preference to it.
#
# Deliberately confined to the MEASUREMENT channel, and unreachable from a flow.
# A continuous flow carries a conserved quantity: taking the median of three
# pipes delivering water would create or destroy matter, and a continuous input
# is therefore, and permanently, the SUM of its connections. See
# ``FlowModel.check_combine_is_not_a_flow_policy``, which refuses the key by
# name rather than leaving the restriction to a docstring.


#: Add the readings up. What a single-connection channel has always done, and
#: therefore the only policy that leaves an existing model unchanged.
COMBINE_SUM = "sum"

#: The arithmetic mean of the readings. Rejects nothing: one wild reading drags
#: the estimate by its full deviation divided by the connection count.
COMBINE_MEAN = "mean"

#: The median of the readings -- the estimator redundant sensors exist for. With
#: an odd number of readings a single stuck or wild one cannot move it at all,
#: which is precisely what a mean does not give.
COMBINE_MEDIAN = "median"

#: The smallest reading. A conservative reading of a redundant set.
COMBINE_MIN = "min"

#: The largest reading. The pessimistic counterpart of :data:`COMBINE_MIN`.
COMBINE_MAX = "max"

#: Every policy name a measurement channel may declare.
COMBINE_POLICIES = (
    COMBINE_SUM,
    COMBINE_MEAN,
    COMBINE_MEDIAN,
    COMBINE_MIN,
    COMBINE_MAX,
)


def combine_sum(values):
    """Sum of the readings."""
    return float(sum(values))


def combine_mean(values):
    """Arithmetic mean of the readings."""
    return float(statistics.fmean(values))


def combine_median(values):
    """Median of the readings.

    On an even count this is the mean of the two central readings, as
    ``statistics.median`` defines it. Two sensors therefore give no rejection at
    all -- a redundant set is sized odd for a reason, and the library does not
    pretend otherwise by inventing a tie-break.
    """
    return float(statistics.median(values))


def combine_min(values):
    """Smallest reading."""
    return float(min(values))


def combine_max(values):
    """Largest reading."""
    return float(max(values))


#: The named policies, resolved to the function each one designates.
COMBINE_FUNCTIONS = {
    COMBINE_SUM: combine_sum,
    COMBINE_MEAN: combine_mean,
    COMBINE_MEDIAN: combine_median,
    COMBINE_MIN: combine_min,
    COMBINE_MAX: combine_max,
}


def combine(values, policy=None, combine_fun=None, default=0.0):
    """Reduce the readings of one measurement channel to a single number.

    Parameters
    ----------
    values : sequence of float
        One reading per connection, in connection order.
    policy : str, optional
        A name from :data:`COMBINE_POLICIES`. Defaults to :data:`COMBINE_SUM`,
        which is the identity on a single reading.
    combine_fun : callable, optional
        ``f(values) -> float``, the Python extension point, used in
        **preference** to the named policy -- exactly as ``allocation_fun`` is
        used in preference to ``allocation``.
    default : float, optional
        What an EMPTY reading list combines to. A channel connected to nobody
        reads its declared default rather than raising, which is what a
        single-connection measurement has always done.

    Returns
    -------
    float
    """
    values = list(values)

    if not values:
        return float(default)

    if combine_fun is not None:
        return float(combine_fun(values))

    return COMBINE_FUNCTIONS[COMBINE_SUM if policy is None else policy](values)

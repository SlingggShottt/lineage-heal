{#
    Fails for every date on which the calendar says a file was expected and none is stored.
    This is the late-data fault class: a holiday or weekend is not a failure, a trading day
    with no file is.

    Run it on slv_meta__calendar, which holds the judging window: from each feed's first stored
    file to the run's as_of_date, or to the newest date any feed has. Because the end can be
    the newest date any feed has, a feed that is behind the others is caught, but a run in
    which every feed is missing the same newest day is not; the scheduled pipeline therefore
    supplies as_of_date (`--vars '{as_of_date: 2026-10-16}'`).

    Run it with severity warn: a late file is a signal for the Detector, not a reason to stop
    the layers downstream from building on the data that did arrive.
#}
{% test file_present_when_expected(model) %}

select source, dataset, calendar_date, expectation
from {{ model }}
where file_expected and not file_present

{% endtest %}

import os
import logging

from chameleon import PageTemplateFile

from mist.api.rules.models import Rule
from mist.api.users.models import User

from mist.api.notifications.models import EmailAlert
from mist.api.notifications.models import InAppRecommendation

from mist.api.notifications.helpers import _log_alert
from mist.api.notifications.helpers import _get_alert_details


log = logging.getLogger(__name__)


def send_alert_email(rule, resource, incident_id, value, triggered, timestamp,
                     emails, action=''):
    """Send an alert e-mail to notify users that a rule was triggered.

    Arguments:

        rule:        The mist.api.rules.models.Rule instance that got
                     triggered.
        resource:    The resource for which the rule got triggered.
                     For a subclass of `ResourceRule` his has to be a
                     `me.Document` subclass. If the rule is arbitrary,
                     then this argument must be set to None.
        incident_id: The UUID of the incident. Each new incident gets
                     assigned a UUID.
        value:       The value yielded by the rule's evaluation. This
                     is the value that's exceeded the given threshold.
        triggered:   True, if the rule has been triggered. Otherwise,
                     False.
        timestamp:   The UNIX timestamp at which the state of the rule
                     changed, went from triggered to un-triggered or
                     vice versa.
        emails:      A list of e-mails to push notifications to.
        action:      An optional action to replace the default "alert".

    Note that alerts aren't sent out every time a rule gets triggered,
    rather they obey the `EmailAlert.reminder_schedule` schedule that
    denotes how often an e-mail may be sent.

    """
    assert isinstance(rule, Rule), type(rule)
    assert resource or rule.is_arbitrary(), type(resource)

    # Get dict with alert details.
    info = _get_alert_details(resource, rule, incident_id, value,
                              triggered, timestamp, action)

    # Create a new EmailAlert if the alert has just been triggered.
    try:
        alert = EmailAlert.objects.get(owner=rule.owner_id,
                                       incident_id=incident_id)
    except EmailAlert.DoesNotExist:
        alert = EmailAlert(owner=rule.owner, incident_id=incident_id)
        # Allows unsubscription from alerts on a per-rule basis.
        alert.rid = rule.id
        alert.rtype = 'rule'
        # Allows reminder alerts to be sent.
        alert.reminder_enabled = True
        # Allows to log newly triggered incidents.
        skip_log = False
    else:
        skip_log = False if not triggered else True
        reminder = ' - Reminder %d' % alert.reminder_count if triggered else ''
        info['action'] += reminder

    # Check whether an alert has to be sent in case of a (re)triggered rule.
    if triggered and not alert.is_due():
        log.info('Alert for %s is due in %s', rule, alert.due_in())
        return

    # Create the e-mail body.
    subject = '[mist.io] *** %(state)s *** from %(name)s: %(metric_name)s'
    alert.subject = subject % info

    pt = os.path.join(os.path.dirname(__file__), 'templates/text_alert.pt')
    alert.text_body = PageTemplateFile(pt)(inputs=info)

    pt = os.path.join(os.path.dirname(__file__), 'templates/html_alert.pt')
    alert.html_body = PageTemplateFile(pt)(inputs=info)

    # Send alert.
    alert.channel.send(list(emails))

    # We need to save the notification's state in order to look it up the next
    # time an alert will be re-triggered or untriggered for the given incident.
    # We also make sure to delete the notification in case the corresponding
    # alert has been untriggered, since (at least for now) there is no reason
    # to keep notifications via e-mail indefinetely.
    if triggered:
        alert.reminder_count += 1
        alert.save()
    else:
        alert.delete()

    # Log (un)triggered alert.
    if skip_log is False:
        _log_alert(resource, rule, value, triggered, timestamp, incident_id,
                   action)


def dismiss_scale_notifications(machine, feedback='NEUTRAL'):
    '''
    Convenience function to dismiss scale notifications from
    a machine.
    Calls dismiss on each notification's channel. May update
    the feedback field on each notification.
    '''
    recommendation = InAppRecommendation.objects(
        owner=machine.owner, model_id="autoscale_v1", rid=machine.id).first()
    # TODO Shouldn't we store which user executed the recommendations action?
    # Marking the recommendation as "dismissed by everyone" seems a bit wrong.
    # Perhaps recommendations' actions such as this one must be invoked by a
    # distinct API endpoint?
    recommendation.applied = feedback == "POSITIVE"
    user_ids = set(user.id for user in machine.owner.members)
    user_ids ^= set(recommendation.dismissed_by)
    recommendation.channel.dismiss(
        users=[user for user in User.objects(id__in=user_ids).only('id')]
    )

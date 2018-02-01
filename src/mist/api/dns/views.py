import mongoengine as me
from pyramid.response import Response

from mist.api.clouds.models import Cloud
from mist.api.dns.models import Zone, Record, RECORDS

from mist.api.auth.methods import auth_context_from_request
from mist.api.dns.methods import filter_list_zones
from mist.api.dns.methods import filter_list_records

from mist.api.exceptions import NotFoundError
from mist.api.exceptions import CloudNotFoundError

from mist.api.tag.methods import resolve_id_and_set_tags

from mist.api.helpers import trigger_session_update
from mist.api.helpers import params_from_request, view_config

OK = Response("OK", 200)


@view_config(route_name='api_v1_zones', request_method='GET', renderer='json')
def list_dns_zones(request):
    """
    List all DNS zones.
    Retrieves a list of all DNS zones based on the user Clouds.
    For each cloud that supports DNS functionality, we get all available zones.
    ---
    """
    auth_context = auth_context_from_request(request)
    cloud_id = request.matchdict['cloud']

    zones = filter_list_zones(auth_context, cloud_id)
    return zones


@view_config(route_name='api_v1_records', request_method='GET',
             renderer='json')
def list_dns_records(request):
    """
    List all DNS zone records for a particular zone.
    ---
    """
    auth_context = auth_context_from_request(request)
    cloud_id = request.matchdict['cloud']
    zone_id = request.matchdict['zone']
    try:
        cloud = Cloud.objects.get(owner=auth_context.owner, id=cloud_id)
    except me.DoesNotExist:
        raise CloudNotFoundError
    try:
        zone = Zone.objects.get(owner=auth_context.owner, cloud=cloud,
                                id=zone_id)
    except Zone.DoesNotExist:
        raise NotFoundError('Zone does not exist')

    return filter_list_records(auth_context, zone)


@view_config(route_name='api_v1_zones', request_method='POST', renderer='json')
def create_dns_zone(request):
    """
    Create a new DNS zone under a specific cloud.
    ---
    """
    auth_context = auth_context_from_request(request)

    cloud_id = request.matchdict['cloud']
    auth_context.check_perm("cloud", "read", cloud_id)
    auth_context.check_perm("cloud", "create_resources", cloud_id)
    tags = auth_context.check_perm("zone", "add", None)
    # Try to get the specific cloud for which we will create the zone.
    try:
        cloud = Cloud.objects.get(owner=auth_context.owner, id=cloud_id)
    except me.DoesNotExist:
        raise CloudNotFoundError

    params = params_from_request(request)
    new_zone = Zone.add(owner=cloud.owner, cloud=cloud, **params).as_dict()

    if tags:
        resolve_id_and_set_tags(auth_context.owner, 'zone', new_zone['id'],
                                tags, cloud_id=cloud_id)

    # Schedule a UI update
    trigger_session_update(auth_context.owner, ['zones'])
    return new_zone


@view_config(route_name='api_v1_records', request_method='POST',
             renderer='json')
def create_dns_record(request):
    """
    Create a new record under a specific zone
    ---
    """
    auth_context = auth_context_from_request(request)

    cloud_id = request.matchdict['cloud']
    # Try to get the specific cloud for which we will create the zone.
    try:
        cloud = Cloud.objects.get(owner=auth_context.owner, id=cloud_id)
    except me.DoesNotExist:
        raise CloudNotFoundError()

    zone_id = request.matchdict['zone']
    try:
        zone = Zone.objects.get(owner=auth_context.owner, id=zone_id,
                                cloud=cloud)
    except Zone.DoesNotExist:
        raise NotFoundError('Zone does not exist')

    auth_context.check_perm("cloud", "read", cloud_id)
    auth_context.check_perm("zone", "read", zone_id)
    auth_context.check_perm("zone", "create_records", zone_id)
    tags = auth_context.check_perm("record", "add", None)
    # Get the params and create the new record
    params = params_from_request(request)
    dns_cls = RECORDS[params['type']]

    rec = dns_cls.add(owner=auth_context.owner, zone=zone, **params).as_dict()

    if tags:
        resolve_id_and_set_tags(auth_context.owner, 'record', rec['id'], tags,
                                cloud_id=cloud_id, zone_id=zone_id)

    # Schedule a UI update
    trigger_session_update(auth_context.owner, ['zones'])
    return rec


@view_config(route_name='api_v1_zone', request_method='DELETE',
             renderer='json')
def delete_dns_zone(request):
    """
    Delete a specific DNS zone under a cloud.
    ---
    """
    auth_context = auth_context_from_request(request)
    cloud_id = request.matchdict['cloud']
    zone_id = request.matchdict['zone']
    # Do we need the cloud here, now that the models have been created?
    try:
        cloud = Cloud.objects.get(owner=auth_context.owner, id=cloud_id)
    except me.DoesNotExist:
        raise CloudNotFoundError()
    try:
        zone = Zone.objects.get(owner=auth_context.owner, id=zone_id,
                                cloud=cloud)
    except Zone.DoesNotExist:
        raise NotFoundError('Zone does not exist')

    auth_context.check_perm("zone", "remove", zone_id)

    zone.ctl.delete_zone()

    # Schedule a UI update
    trigger_session_update(auth_context.owner, ['zones'])
    return OK


@view_config(route_name='api_v1_record', request_method='DELETE',
             renderer='json')
def delete_dns_record(request):
    """
    Delete a specific DNS record under a zone.
    ---
    """
    auth_context = auth_context_from_request(request)
    cloud_id = request.matchdict['cloud']
    zone_id = request.matchdict['zone']
    record_id = request.matchdict['record']
    try:
        cloud = Cloud.objects.get(owner=auth_context.owner, id=cloud_id)
    except me.DoesNotExist:
        raise CloudNotFoundError()
    try:
        zone = Zone.objects.get(owner=auth_context.owner, id=zone_id,
                                cloud=cloud)
    except Zone.DoesNotExist:
        raise NotFoundError('Zone does not exist')
    try:
        record = Record.objects.get(zone=zone, id=record_id)
    except Record.DoesNotExist:
        raise NotFoundError('Record does not exist')

    auth_context.check_perm("record", "remove", record_id)

    record.ctl.delete_record()

    # Schedule a UI update
    trigger_session_update(auth_context.owner, ['zones'])
    return OK

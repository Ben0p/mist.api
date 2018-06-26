from mist.api.exceptions import CloudNotFoundError

from mist.api.clouds.models import Cloud

from mist.api.methods import connect_provider

from libcloud.compute.types import Provider


def list_networks(owner, cloud_id):
    """List the networks of the specified cloud"""
    ret = {'public': {}, 'private': {}, 'routers': {}}  # FIXME

    try:
        cloud = Cloud.objects.get(owner=owner, id=cloud_id)
    except Cloud.DoesNotExist:
        raise CloudNotFoundError()

    if not hasattr(cloud.ctl, 'network'):
        return ret

    networks = cloud.ctl.network.list_networks()

    for network in networks:

        network_dict = network.as_dict()
        if hasattr(network, 'location'):
            network_dict['location'] = network.location

        network_dict['subnets'] = {}
        for subnet in network.ctl.list_subnets():
            subnet_dict = subnet.as_dict()
            network_dict['subnets'].update({subnet_dict['id']: subnet_dict})

    # TODO: Backwards-compatible network privacy detection, to be replaced
        if not network_dict.get('router_external'):
            ret['private'].update({network_dict['id']: network_dict})
        else:
            ret['public'].update({network_dict['id']: network_dict})
    return ret


def filter_list_networks(auth_context, cloud_id, networks=None, perm='read'):
    """Filter the networks of the specific cloud based on RBAC policy"""
    if networks is None:
        networks = list_networks(auth_context.owner, cloud_id)
    if not auth_context.is_owner():
        allowed_resources = auth_context.get_allowed_resources(perm)
        if cloud_id not in allowed_resources['clouds']:
            return {'public': [], 'private': [], 'routers': []}
        for key in ('public', 'private', ):
            if not networks.get(key):
                continue
            for i in xrange(len(networks[key]) - 1, -1, -1):
                if networks[key][i]['id'] not in allowed_resources['networks']:
                    networks[key].pop(i)
    return networks


def associate_ip(owner, cloud_id, network_id, ip,
                 machine_id=None, assign=True):
    cloud = Cloud.objects.get(owner=owner, id=cloud_id, deleted=None)
    conn = connect_provider(cloud)

    if conn.type != Provider.NEPHOSCALE:
        return False

    return conn.ex_associate_ip(ip, server=machine_id, assign=assign)

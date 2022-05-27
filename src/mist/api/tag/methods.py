import logging
import re
from mongoengine import Q
import mongoengine as me
from mist.api.tag.models import Tag
from mist.api.helpers import trigger_session_update
from mist.api.helpers import get_object_with_id, search_parser
from functools import reduce
from mist.api.config import TAGS_RESOURCE_TYPES
from mist.api import config
from mist.api.helpers import get_resource_model


log = logging.getLogger(__name__)


def get_tags(auth_context, verbose='', resource='', search='', sort='key', start=None, limit=None, only=None, deref=None):  # noqa: E501
    query = Q(owner=auth_context.owner)

    if config.HAS_RBAC and not auth_context.is_owner():
        rbac_filter = Q()
        for rtype, rids in auth_context.get_allowed_resources().items():
            if rids:
                rbac_filter |= Q(resource_type=rtype.rstrip('s'),
                                 resource_id__in=rids)
        query &= rbac_filter

    missing_resources = get_missing_resources()
    if missing_resources:
        missing_query = Q()
        for rtype, rids in missing_resources.items():
            missing_query |= Q(resource_type=rtype, resource_id__nin=rids)

        query &= missing_query

    if resource:
        query &= Q(resource_type=resource.rstrip('s'))

    # search filter contains space separated terms
    # if the term contains :,=,<,>,!=, <=, >= then assume key/value query
    # otherwise search for objects with id or name matching the term
    terms = search_parser(search)
    for term in terms:
        if "!=" in term:
            k, v = term.split("!=")
            mongo_operator = '__ne'
        elif ':' in term or '=' in term:
            k, v = re.split(r'[:,=]', term)
            mongo_operator = ''
        query &= Q(**{f'{k}{mongo_operator}': v})

    if sort[0] in ['+', '-'] and sort[1:] in ['key', 'resource_count']:
        reverse = True if sort[0] == '-' else False
        sort = sort[1:]
    elif sort in ['key', 'resource_count']:
        reverse = False
    else:
        sort = 'key'
        reverse = False

    tags = Tag.objects(query)
    data = [{'key': k, 'value': v} for k, v in
            set((t.key, t.value) for t in tags)]

    if verbose:
        from mist.api.methods import list_resources

        for kv in data:
            kv_temp = kv.copy()
            kv['resources'] = {}
            for resource_type in TAGS_RESOURCE_TYPES:
                kv['resources'][resource_type + 's'] = []
                if deref == 'name' and resource_type == 'zone':
                    attr = 'domain'
                else:
                    attr = deref

                for tag in tags.filter(**kv_temp, resource_type=resource_type):
                    try:
                        resource = list_resources(
                            auth_context=auth_context,
                            resource_type=resource_type,
                            search=tag.resource_id, only=attr
                        )[0]
                        if resource:
                            kv['resources'][resource_type + 's'].append(
                                getattr(resource.get(), attr))
                    except KeyError:
                        continue
                    except me.DoesNotExist:
                        log.error('%s with id %s does not exist',
                                  resource_type, tag.resource_id)

    if sort == "resource_count" and verbose:
        data.sort(key=lambda x: sum(map(len, x['resources'])), reverse=reverse)
    elif sort == 'key':
        data.sort(key=lambda x: x['key'], reverse=reverse)

    try:
        start = int(start)
        limit = int(limit)
    except (ValueError, TypeError):
        start = 0
        limit = 100

    if only and data:
        only_list = [field for field in data[0]
                     if field in only.split(',')]
        data = [{k: v for k, v in d.items() if k in only_list} for d in data]

    meta = {
        'total': len(data),
        'returned': len(data[start:start + limit]),
        'sort': sort,
        'start': start
    }

    return data[start:start + limit], meta


def get_tags_for_resource(owner, resource_obj, *args, **kwargs):
    return {tag.key: tag.value
            for tag in get_tag_objects_for_resource(
                owner, resource_obj, args, kwargs)}


def get_tag_objects_for_resource(owner, resource_obj, *args, **kwargs):
    return Tag.objects(
        owner=owner,
        resource_type=resource_obj.to_dbref().collection.rstrip('s'),
        resource_id=resource_obj.id)


def add_tags_to_resource(owner, resource_obj, tags, *args, **kwargs):
    """
    This function get a list of tags in the form
    [{'joe': 'schmoe'}, ...] and will scan the list and update all
    the tags whose keys are present but whose values are different and add all
    the missing ones
    :param owner: the resource owner
    :param resource_obj: the resource object where the tags will be added
    :param tags: list of tags to be added
    """
    # merge all the tags in the list into one dict. this will also make sure
    # that if there are duplicates they will be cleaned up
    rtype = resource_obj._meta["collection"].rstrip('s')
    existing_tags = get_tag_objects_for_resource(owner, resource_obj)

    tag_dict = {
        k: v for k, v
        in dict(tags).items() - {
            tag.key: tag.value for tag in existing_tags}.items()
    }
    if tag_dict:
        remove_tags_from_resource(owner, resource_obj, tag_dict)

        Tag.objects.insert([Tag(owner=owner, resource_id=resource_obj.id,
                                resource_type=rtype, key=key, value=value)
                            for key, value in tag_dict.items()])

        # SEC
        owner.mapper.update(resource_obj)

        trigger_session_update(owner,
                               [rtype + 's' if not rtype.endswith('s')
                                else rtype])


def remove_tags_from_resource(owner, resource_obj, tags, *args, **kwargs):
    """
    This function get a list of tags in the form {'key1': 'value1',
    'key2': 'value2'} and will delete them from the resource
    :param owner: the resource owner
    :param resource_obj: the resource object where the tags will be added
    :param rtype: resource type
    :param tags: list of tags to be deleted
    """
    # ensure there are no duplicate tag keys because mongoengine will
    # raise exception for duplicates in query
    key_list = list(set(tags))

    # create a query that will return all the tags with
    query = reduce(lambda q1, q2: q1.__or__(q2),
                   [Q(key=key) for key in key_list])

    get_tag_objects_for_resource(owner, resource_obj).filter(query).delete()

    # SEC
    owner.mapper.update(resource_obj)

    rtype = resource_obj._meta["collection"]

    trigger_session_update(owner,
                           [rtype + 's' if not rtype.endswith('s') else rtype])


def resolve_id_and_get_tags(owner, rtype, rid, *args, **kwargs):
    """
    This call will try to fetch the object of type rtype from the db with id
    rid. If the object is of type machine, image, network or location the
    cloud_id must also be provided in the kwargs. If the resource type is
    machine then the machine_id must be provided and not the object id. Whether
    or not the owner has the necessary credentials to get the tags of the
    resource is left to the caller of this function to validate.
    :param owner: the owner of the resource
    :param rtype: resource type
    :param rid: resource id
    :return: the tags of this resource
    """
    resource_obj = get_object_with_id(owner, rid, rtype, *args, **kwargs)
    return get_tags_for_resource(owner, resource_obj)


def resolve_id_and_set_tags(owner, rtype, rid, tags, *args, **kwargs):
    """
    :param owner: the owner of the resource
    :param rtype: resource type
    :param rid: resource id
    :param tags: resource tags to be added or updated
    :return: the tags to be added or updated to this resource
    """
    resource_obj = get_object_with_id(owner, rid, rtype, *args, **kwargs)
    return add_tags_to_resource(owner, resource_obj, tags, *args,
                                **kwargs)


def resolve_id_and_delete_tags(owner, rtype, rid, tags, *args, **kwargs):
    """
    :param owner: the owner of the resource
    :param rtype: resource type
    :param rid: resource id
    :param tags: resource id
    :return: the tags to be deleted from this resource
    """
    resource_obj = get_object_with_id(owner, rid, rtype, *args, **kwargs)
    return remove_tags_from_resource(owner, resource_obj, tags,
                                     *args, **kwargs)


def modify_security_tags(auth_context, tags, resource=None):
    """
    This method splits the resources' tags in security and non-security
    groups. Security tags are part of team policies. Such tags should only
    be modified by organization owners in order to enforce team policies.
    If a team member attempts to edit a security tag, an UnauthorizedError
    will be thrown
    :param tags: the new tags dict
    :param resource: the resource on which the tags are going to be applied
    :return: False, if a security tag has been modified in the new tags
    dict by someone other than the organization owner, otherwise True
    """
    # private context
    if auth_context.org is None:
        return True

    if auth_context.is_owner():
        return True
    else:
        rtags = get_tag_objects_for_resource(
            auth_context.owner, resource).only('key', 'value')
        rtags = {rtag.key: rtag.value for rtag in rtags}
        security_tags = auth_context.get_security_tags()
        # check whether the new tags tend to modify any of the security_tags
        for security_tag in security_tags:
            for key, value in list(security_tag.items()):
                if key not in list(rtags.keys()):
                    if key in list(tags.keys()):
                        return False
                else:
                    if key not in list(tags.keys()):
                        return False
                    elif value != tags[key]:
                        return False
        return True


def delete_security_tag(auth_context, tag_key):
    """
    This method checks whether the tag to be deleted belongs to the
    secure tags group
    :param tag_key: the key of the tag to be removed
    :return: False in case a security tag is about to be deleted
    """
    # private context
    if auth_context.org is None:
        return True

    if auth_context.is_owner():
        return True
    else:
        security_tags = auth_context.get_security_tags()
        for security_tag in security_tags:
            for key, value in list(security_tag.items()):
                if key == tag_key:
                    return False
        return True


def get_missing_resources():

    dikt = {rtype: [] for rtype in TAGS_RESOURCE_TYPES}
    states_rtypes = {
        'deleted': ['cloud', 'key', 'script', 'template'],
        'missing_since': ['machine', 'cluster', 'network',
                          'volume', 'image', 'subnet',
                          'location', 'size']
    }
    for state, rtypes in states_rtypes.items():
        condition = None if state == 'missing_since' else False
        for resource_type in rtypes:
            query = Q(**{f'{state}__ne': condition})
            if resource_type == 'cloud':
                query |= Q(**{'enabled': False})
            try:
                resource_objs = get_resource_model(
                    resource_type).objects(query)
            except KeyError:
                continue
            if resource_objs:
                dikt[resource_type] += [obj.id for obj in resource_objs]

    return dikt

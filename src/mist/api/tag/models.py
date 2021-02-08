import re

import mongoengine as me

from mist.api.users.models import Owner


class Tag(me.Document):

    owner = me.ReferenceField(Owner, required=True,
                              reverse_delete_rule=me.CASCADE)
    key = me.StringField(required=True)

    resource_type = me.StringField(
        choices=['cloud', 'machine', 'volume', 'objectstorage', 'image',
                 'network', 'subnet', 'zone', 'record',
                 'key', 'script', 'template', 'stack',
                 'schedule', 'tunnel', 'rule', 'team'])

    value = me.StringField()
    resource_id = me.StringField()

    meta = {
        'indexes': ['owner', 'resource_type', 'resource_id', 'key']
    }

    @property
    def resource(self):
        resource_type = self.resource_type.capitalize().rstrip('s')
        from mist.api import models
        return getattr(models, resource_type).objects.get(id=self.resource_id)

    def validate(self, clean=False):
        if not re.search(r'^[a-zA-Z0-9_]+(?:[ :.-][a-zA-Z0-9_]+)*$',
                         self.key):
            raise me.ValidationError('Invalid key name')

    def __str__(self):
        return 'Tag %s:%s for %s' % (self.key, self.value, self.resource)

    def as_dict(self):
        return {
            'key': self.key,
            'value': self.value,
            'owner': self.owner.id,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
        }

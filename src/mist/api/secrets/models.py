import logging
from uuid import uuid4
from typing import Any, Dict

import mongoengine as me
from mist.api.exceptions import BadRequestError
from mist.api.users.models import Owner
from mist.api.ownership.mixins import OwnershipMixin
from mist.api.tag.models import Tag
from mist.api.tag.mixins import TagMixin

log = logging.getLogger(__name__)


class Secret(OwnershipMixin, me.Document, TagMixin):
    """ A Secret object """
    id = me.StringField(primary_key=True,
                        default=lambda: uuid4().hex)
    name = me.StringField(required=True)
    owner = me.ReferenceField(Owner, reverse_delete_rule=me.CASCADE)

    meta = {
        'strict': False,
        'allow_inheritance': True,
        'collection': 'secrets',
        'indexes': [
            'owner',
            {
                'fields': ['owner', 'name'],
                'sparse': False,
                'unique': True,
                'cls': False,
            }, {
                'fields': ['$tags'],
                'default_language': 'english',
                'sparse': True,
                'unique': False
            }
        ],
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Calculate and store key type specific fields.
        self._secret_specific_fields = [field for field in type(self)._fields
                                        if field not in Secret._fields]

    @property
    def data(self):
        raise NotImplementedError()

    def create_or_update(self, attributes: Dict[str, Any]) -> None:
        raise NotImplementedError()

    def delete(self, delete_from_engine: bool = False) -> None:
        super().delete()
        self.owner.mapper.remove(self)
        Tag.objects(resource_id=self.id, resource_type='secret').delete()

    def __str__(self):
        return '%s secret %s (%s) of %s' % (type(self), self.name,
                                            self.id, self.owner)


class VaultSecret(Secret):
    """ A Vault Secret object """

    @property
    def data(self) -> Dict[str, Any]:
        try:
            data = self.owner.secrets_ctl.read_secret(self.name)
        except BadRequestError:
            data = {}
        return data

    def create_or_update(self, attributes: Dict[str, Any]) -> None:
        return self.owner.secrets_ctl.create_or_update_secret(
            self.name,
            attributes)

    def delete(self, delete_from_engine: bool = False) -> None:
        super().delete()
        if delete_from_engine:
            self.owner.secrets_ctl.delete_secret(self.name)

    def as_dict(self) -> Dict[str, Any]:
        s_dict = {
            'id': self.id,
            'name': self.name,
            'tags': {
                tag.key: tag.value
                for tag in Tag.objects(resource_id=self.id,
                                       resource_type='secret')},
            'owned_by': self.owned_by.id if self.owned_by else '',
            'created_by': self.created_by.id if self.created_by else '',
        }
        return s_dict


class SecretValue(me.EmbeddedDocument):
    """ Retrieve the value of a Secret object """
    secret = me.ReferenceField('Secret', required=False)
    key = me.StringField()

    def __init__(self, secret: Secret, key: str = '', *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.secret = secret
        if key:
            self.key = key

    @property
    def value(self):
        if self.key:
            return self.secret.data[self.key]
        else:
            return self.secret.data

    def __str__(self):
        return '%s secret value of %s' % (type(self),
                                          self.secret.name)

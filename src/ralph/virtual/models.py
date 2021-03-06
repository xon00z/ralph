# -*- coding: utf-8 -*-
import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.dispatch import receiver
from django.utils.translation import ugettext_lazy as _

from ralph.admin.helpers import get_value_by_relation_path
from ralph.assets.models.base import BaseObject
from ralph.assets.models.choices import ComponentType
from ralph.assets.models.components import Component, ComponentModel
from ralph.data_center.models.networks import IPAddress
from ralph.data_center.models.physical import DataCenterAsset
from ralph.lib.mixins.models import AdminAbsoluteUrlMixin, NamedMixin

logger = logging.getLogger(__name__)


class CloudProvider(NamedMixin):
    class Meta:
        verbose_name = _('Cloud provider')
        verbose_name_plural = _('Cloud providers')


class CloudFlavor(BaseObject):
    name = models.CharField(_('name'), max_length=255)
    cloudprovider = models.ForeignKey(CloudProvider)
    cloudprovider._autocomplete = False

    flavor_id = models.CharField(unique=True, max_length=100)

    def __str__(self):
        return self.name

    def _set_component(self, model_args):
        """ create/modify component cpu, mem or disk"""
        try:
            model = ComponentModel.objects.get(name=model_args['name'])
        except ObjectDoesNotExist:
            model = ComponentModel()
            for key, value in model_args.items():
                setattr(model, key, value)
            model.save()
        try:
            VirtualComponent.objects.get(base_object=self, model=model)
        except ObjectDoesNotExist:
            for component in self.virtualcomponent.filter(
                model__type=model_args['type']
            ):
                component.delete()

            VirtualComponent(base_object=self, model=model).save()

    def _get_component(self, model_type, field_path):
        # use cached components if already prefetched (using prefetch_related)
        # otherwise, perform regular SQL query
        try:
            components = self._prefetched_objects_cache['virtualcomponent']
        except (KeyError, AttributeError):
            return self.virtualcomponent.filter(
                model__type=model_type
            ).values_list(field_path, flat=True).first()
        else:
            for component in components:
                if component.model.type == model_type:
                    return get_value_by_relation_path(component, field_path)
            return None

    @property
    def cores(self):
        """Number of cores"""
        return self._get_component(ComponentType.processor, 'model__cores')

    @cores.setter
    def cores(self, new_cores):
        cpu = {
            'name': "{} cores vCPU".format(new_cores),
            'cores': new_cores,
            'family': 'vcpu',
            'type': ComponentType.processor
        }
        if self.cores != new_cores:
            self._set_component(cpu)

    @property
    def memory(self):
        """RAM memory size in MiB"""
        return self._get_component(ComponentType.memory, 'model__size')

    @memory.setter
    def memory(self, new_memory):
        ram = {
            'name': "{} MiB vMEM".format(new_memory),
            'size': new_memory,
            'type': ComponentType.memory,
        }
        if self.memory != new_memory:
            self._set_component(ram)

    @property
    def disk(self):
        """Disk size in MiB"""
        return self._get_component(ComponentType.disk, 'model__size')

    @disk.setter
    def disk(self, new_disk):
        disk = {
            'name': "{} GiB vHDD".format(int(new_disk/1024)),
            'size': new_disk,
            'type': ComponentType.disk,
        }
        if self.disk != new_disk:
            self._set_component(disk)


class CloudProject(AdminAbsoluteUrlMixin, BaseObject):
    cloudprovider = models.ForeignKey(CloudProvider)
    cloudprovider._autocomplete = False

    project_id = models.CharField(unique=True, max_length=100)
    name = models.CharField(max_length=100)

    def __str__(self):
        return 'Cloud Project: {}'.format(self.name)


@receiver(models.signals.post_save, sender=CloudProject)
def update_service_env_on_cloudproject_save(sender, instance, **kwargs):
    """Update CloudHost service_env while updating CloudProject"""
    if instance.pk is not None:
        instance.children.all().update(service_env=instance.service_env)


class CloudHost(AdminAbsoluteUrlMixin, BaseObject):
    def save(self, *args, **kwargs):
        try:
            self.service_env = self.parent.service_env
        except AttributeError:
            pass
        super(CloudHost, self).save(*args, **kwargs)

    cloudflavor = models.ForeignKey(CloudFlavor, verbose_name='Instance Type')
    cloudprovider = models.ForeignKey(CloudProvider)
    cloudprovider._autocomplete = False

    host_id = models.CharField(unique=True, max_length=100)
    hostname = models.CharField(max_length=100)
    hypervisor = models.ForeignKey(DataCenterAsset, blank=True, null=True)
    image_name = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        verbose_name = _('Cloud host')
        verbose_name_plural = _('Cloud hosts')

    def __str__(self):
        return 'Cloud Host: {}'.format(self.hostname)

    @property
    def ip_addresses(self):
        return [ip.address for ip in self.ipaddress_set.all()]

    @ip_addresses.setter
    def ip_addresses(self, value):
        if set(self.ip_addresses) == set(value):
            return
        for ip in set(value)-set(self.ip_addresses):
            try:
                new_ip = IPAddress.objects.get(address=ip)
                if new_ip.base_object is None:
                    new_ip.base_object = self
                    new_ip.save()
                else:
                    logger.warning((
                        'Cannot assign IP %s to %s - it is already in use by '
                        'another asset'
                    ) % (ip, self.hostname))
            except ObjectDoesNotExist:
                new_ip = IPAddress(base_object=self, address=ip)
                new_ip.save()

        for ip in set(self.ip_addresses) - set(value):
            release_ip = IPAddress.objects.get(base_object=self, address=ip)
            release_ip.base_object = None
            release_ip.save()

    @property
    def cloudproject(self):
        """Workaround, because parent resolves to BaseObject in rest api"""
        try:
            return self.parent.cloudproject
        except AttributeError:
            return None


class VirtualComponent(Component):
    pass


class VirtualServer(BaseObject):
    # TODO This model has to be developed, eg. add hostanme, hypervisior e.t.c.
    class Meta:
        verbose_name = _('Virtual server (VM)')
        verbose_name_plural = _('Virtual servers (VM)')

    def __str__(self):
        return 'VirtualServer: {}'.format(self.service_env)

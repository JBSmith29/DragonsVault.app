"""Unmanaged models mapping to the existing DragonsVault tables."""

from django.db import models


class User(models.Model):
    id = models.AutoField(primary_key=True)
    email = models.CharField(max_length=255)
    username = models.CharField(max_length=80)
    is_admin = models.BooleanField()
    api_token_hash = models.CharField(max_length=64, null=True)
    api_token_hint = models.CharField(max_length=12, null=True)

    class Meta:
        managed = False
        db_table = "users"

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False


class Folder(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=20)
    commander_name = models.CharField(max_length=200, null=True)
    deck_tag = models.CharField(max_length=120, null=True)
    owner_user = models.ForeignKey(
        User,
        db_column="owner_user_id",
        null=True,
        on_delete=models.DO_NOTHING,
        related_name="folders",
    )
    is_proxy = models.BooleanField()
    is_public = models.BooleanField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = "folder"


class FolderShare(models.Model):
    id = models.AutoField(primary_key=True)
    folder = models.ForeignKey(
        Folder,
        db_column="folder_id",
        on_delete=models.CASCADE,
        related_name="shares",
    )
    shared_user = models.ForeignKey(
        User,
        db_column="shared_user_id",
        on_delete=models.CASCADE,
        related_name="shared_folders",
    )

    class Meta:
        managed = False
        db_table = "folder_share"


class Card(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    set_code = models.CharField(max_length=10)
    collector_number = models.CharField(max_length=20)
    lang = models.CharField(max_length=5)
    quantity = models.IntegerField()
    is_foil = models.BooleanField()
    folder = models.ForeignKey(
        Folder,
        db_column="folder_id",
        on_delete=models.CASCADE,
        related_name="cards",
    )
    oracle_id = models.CharField(max_length=36, null=True)
    type_line = models.TextField(null=True)
    rarity = models.CharField(max_length=16, null=True)
    color_identity_mask = models.IntegerField(null=True)

    class Meta:
        managed = False
        db_table = "cards"

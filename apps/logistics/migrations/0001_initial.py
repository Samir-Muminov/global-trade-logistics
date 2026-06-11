"""
apps/logistics/migrations/0001_initial.py
 
Initial migration — Global Trade & Logistics Platform, Phase 1.
Manually reviewed for correctness and safe rollback.
 
⚠️ RISK: First migration on a fresh schema — no data loss risk.
Rollback: python manage.py migrate logistics zero
"""
 
import uuid
import django.contrib.postgres.fields
import django.contrib.postgres.indexes
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.db import migrations, models
 
 
class Migration(migrations.Migration):
 
    initial = True
 
    dependencies = [
        # No dependencies — this is the root migration.
    ]
 
    operations = [
 
        # ── PostgreSQL Extensions ──────────────────────────────────────────────
        # These must exist before GIN/trigram indexes are created.
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS pg_trgm;",
            reverse_sql="DROP EXTENSION IF EXISTS pg_trgm;",
        ),
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS btree_gin;",
            reverse_sql="DROP EXTENSION IF EXISTS btree_gin;",
        ),
        migrations.RunSQL(
            sql='CREATE EXTENSION IF NOT EXISTS "uuid-ossp";',
            reverse_sql='DROP EXTENSION IF EXISTS "uuid-ossp";',
        ),
 
        # ── companies ─────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Company",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
                ("legal_name", models.CharField(max_length=255, db_column="legal_name")),
                ("trade_name", models.CharField(max_length=255, blank=True, db_column="trade_name")),
                ("company_type", models.CharField(max_length=10, choices=[("SHIPPER","Shipper"),("CONSIGNEE","Consignee"),("FF","Freight Forwarder"),("CB","Customs Broker"),("3PL","3PL Provider")], db_column="company_type")),
                ("tax_id", models.CharField(max_length=64, unique=True, db_column="tax_id")),
                ("duns_number", models.CharField(max_length=9, blank=True, db_column="duns_number")),
                ("country_code", models.CharField(max_length=2, db_column="country_code")),
                ("is_active", models.BooleanField(default=True, db_column="is_active")),
                ("metadata", models.JSONField(default=dict, blank=True, db_column="metadata")),
            ],
            options={"db_table": "companies", "ordering": ["legal_name"], "verbose_name": "Company", "verbose_name_plural": "Companies"},
        ),
        migrations.AddIndex(model_name="company", index=models.Index(fields=["company_type", "is_active"], name="company_type_active_idx")),
        migrations.AddIndex(model_name="company", index=django.contrib.postgres.indexes.HashIndex(fields=["country_code"], name="company_country_hash_idx")),
        migrations.AddIndex(model_name="company", index=django.contrib.postgres.indexes.GinIndex(fields=["legal_name"], name="company_legal_name_gin_idx", opclasses=["gin_trgm_ops"])),
        migrations.AddIndex(model_name="company", index=django.contrib.postgres.indexes.GinIndex(fields=["metadata"], name="company_metadata_gin_idx")),
        migrations.AddConstraint(model_name="company", constraint=models.CheckConstraint(condition=models.Q(company_type__in=["SHIPPER","CONSIGNEE","FF","CB","3PL"]), name="company_company_type_valid")),
        migrations.AddConstraint(model_name="company", constraint=models.CheckConstraint(condition=models.Q(country_code__regex=r"^[A-Z]{2}$"), name="company_country_code_iso2")),
 
        # ── ports ─────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Port",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
                ("un_locode", models.CharField(max_length=5, unique=True, db_column="un_locode")),
                ("iata_code", models.CharField(max_length=3, blank=True, db_column="iata_code")),
                ("port_name", models.CharField(max_length=255, db_column="port_name")),
                ("country_code", models.CharField(max_length=2, db_column="country_code")),
                ("port_type", models.CharField(max_length=10, choices=[("SEA","Seaport"),("AIR","Airport"),("DRY","Dry Port / ICD"),("MULTI","Multimodal Hub")], db_column="port_type")),
                ("latitude", models.DecimalField(max_digits=9, decimal_places=6, db_column="latitude")),
                ("longitude", models.DecimalField(max_digits=9, decimal_places=6, db_column="longitude")),
                ("timezone", models.CharField(max_length=64, db_column="timezone")),
                ("is_active", models.BooleanField(default=True, db_column="is_active")),
            ],
            options={"db_table": "ports", "ordering": ["port_name"], "verbose_name": "Port", "verbose_name_plural": "Ports"},
        ),
        migrations.AddIndex(model_name="port", index=django.contrib.postgres.indexes.HashIndex(fields=["un_locode"], name="port_unlocode_hash_idx")),
        migrations.AddIndex(model_name="port", index=django.contrib.postgres.indexes.HashIndex(fields=["iata_code"], name="port_iata_hash_idx")),
        migrations.AddIndex(model_name="port", index=models.Index(fields=["port_type", "is_active"], name="port_type_active_idx")),
        migrations.AddIndex(model_name="port", index=models.Index(fields=["country_code", "port_type"], name="port_country_type_idx")),
        migrations.AddConstraint(model_name="port", constraint=models.CheckConstraint(condition=models.Q(latitude__gte=-90) & models.Q(latitude__lte=90), name="port_latitude_valid_range")),
        migrations.AddConstraint(model_name="port", constraint=models.CheckConstraint(condition=models.Q(longitude__gte=-180) & models.Q(longitude__lte=180), name="port_longitude_valid_range")),
        migrations.AddConstraint(model_name="port", constraint=models.CheckConstraint(condition=models.Q(country_code__regex=r"^[A-Z]{2}$"), name="port_country_code_iso2")),
 
        # ── carriers ──────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Carrier",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
                ("company", models.ForeignKey(to="logistics.Company", on_delete=django.db.models.deletion.PROTECT, related_name="carriers", db_column="company_id")),
                ("carrier_code", models.CharField(max_length=10, unique=True, db_column="carrier_code")),
                ("carrier_name", models.CharField(max_length=255, db_column="carrier_name")),
                ("mode", models.CharField(max_length=10, choices=[("SEA","Sea Freight"),("AIR","Air Freight"),("RAIL","Rail Freight"),("ROAD","Road Freight"),("MULTI","Multimodal")], db_column="mode")),
                ("imo_number", models.CharField(max_length=7, blank=True, db_column="imo_number")),
                ("is_active", models.BooleanField(default=True, db_column="is_active")),
                ("hub_ports", django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=5), default=list, blank=True, db_column="hub_ports")),
                ("service_metadata", models.JSONField(default=dict, blank=True, db_column="service_metadata")),
            ],
            options={"db_table": "carriers", "ordering": ["carrier_name"], "verbose_name": "Carrier", "verbose_name_plural": "Carriers"},
        ),
        migrations.AddIndex(model_name="carrier", index=django.contrib.postgres.indexes.HashIndex(fields=["carrier_code"], name="carrier_code_hash_idx")),
        migrations.AddIndex(model_name="carrier", index=django.contrib.postgres.indexes.HashIndex(fields=["imo_number"], name="carrier_imo_hash_idx")),
        migrations.AddIndex(model_name="carrier", index=models.Index(fields=["mode", "is_active"], name="carrier_mode_active_idx")),
        migrations.AddIndex(model_name="carrier", index=django.contrib.postgres.indexes.GinIndex(fields=["hub_ports"], name="carrier_hub_ports_gin_idx")),
        migrations.AddIndex(model_name="carrier", index=django.contrib.postgres.indexes.GinIndex(fields=["service_metadata"], name="carrier_svc_meta_gin_idx")),
        migrations.AddConstraint(model_name="carrier", constraint=models.CheckConstraint(condition=models.Q(mode__in=["SEA","AIR","RAIL","ROAD","MULTI"]), name="carrier_mode_valid")),
        migrations.AddConstraint(model_name="carrier", constraint=models.CheckConstraint(condition=models.Q(imo_number="") | models.Q(imo_number__regex=r"^\d{7}$"), name="carrier_imo_number_format")),
 
        # ── routes ────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Route",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
                ("carrier", models.ForeignKey(to="logistics.Carrier", on_delete=django.db.models.deletion.PROTECT, related_name="routes", db_column="carrier_id")),
                ("origin_port", models.ForeignKey(to="logistics.Port", on_delete=django.db.models.deletion.PROTECT, related_name="outbound_routes", db_column="origin_port_id")),
                ("destination_port", models.ForeignKey(to="logistics.Port", on_delete=django.db.models.deletion.PROTECT, related_name="inbound_routes", db_column="destination_port_id")),
                ("route_code", models.CharField(max_length=32, unique=True, db_column="route_code")),
                ("status", models.CharField(max_length=16, choices=[("ACTIVE","Active"),("SEASONAL","Seasonal"),("SUSPENDED","Suspended"),("DISCONTINUED","Discontinued")], default="ACTIVE", db_column="status")),
                ("transit_days_min", models.PositiveSmallIntegerField(db_column="transit_days_min")),
                ("transit_days_max", models.PositiveSmallIntegerField(db_column="transit_days_max")),
                ("transshipment_ports", django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=5), default=list, blank=True, db_column="transshipment_ports")),
                ("weekly_frequency", models.PositiveSmallIntegerField(default=1, db_column="weekly_frequency")),
                ("effective_from", models.DateField(db_column="effective_from")),
                ("effective_until", models.DateField(null=True, blank=True, db_column="effective_until")),
            ],
            options={"db_table": "routes", "ordering": ["carrier", "route_code"], "verbose_name": "Route", "verbose_name_plural": "Routes"},
        ),
        migrations.AddIndex(model_name="route", index=django.contrib.postgres.indexes.HashIndex(fields=["route_code"], name="route_code_hash_idx")),
        migrations.AddIndex(model_name="route", index=models.Index(fields=["carrier_id", "status"], name="route_carrier_status_idx")),
        migrations.AddIndex(model_name="route", index=models.Index(fields=["origin_port_id", "destination_port_id", "status"], name="route_lane_status_idx")),
        migrations.AddIndex(model_name="route", index=models.Index(fields=["effective_from", "effective_until"], name="route_effective_range_idx")),
        migrations.AddIndex(model_name="route", index=django.contrib.postgres.indexes.GinIndex(fields=["transshipment_ports"], name="route_transshipment_gin_idx")),
        migrations.AddConstraint(model_name="route", constraint=models.CheckConstraint(condition=models.Q(transit_days_min__gt=0), name="route_transit_days_min_positive")),
        migrations.AddConstraint(model_name="route", constraint=models.CheckConstraint(condition=models.Q(transit_days_max__gte=models.F("transit_days_min")), name="route_transit_days_max_gte_min")),
        migrations.AddConstraint(model_name="route", constraint=models.CheckConstraint(condition=models.Q(weekly_frequency__gte=1) & models.Q(weekly_frequency__lte=21), name="route_weekly_frequency_sane")),
        migrations.AddConstraint(model_name="route", constraint=models.CheckConstraint(condition=models.Q(effective_until__isnull=True) | models.Q(effective_until__gt=models.F("effective_from")), name="route_effective_dates_ordered")),
        migrations.AddConstraint(model_name="route", constraint=models.UniqueConstraint(fields=["carrier_id", "origin_port_id", "destination_port_id"], condition=models.Q(status="ACTIVE"), name="route_carrier_active_lane_unique")),
 
        # ── shipments ─────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Shipment",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
                ("shipper", models.ForeignKey(to="logistics.Company", on_delete=django.db.models.deletion.PROTECT, related_name="outbound_shipments", db_column="shipper_id")),
                ("consignee", models.ForeignKey(to="logistics.Company", on_delete=django.db.models.deletion.PROTECT, related_name="inbound_shipments", db_column="consignee_id")),
                ("notify_party", models.ForeignKey(to="logistics.Company", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name="notify_shipments", db_column="notify_party_id")),
                ("carrier", models.ForeignKey(to="logistics.Carrier", on_delete=django.db.models.deletion.PROTECT, related_name="shipments", db_column="carrier_id")),
                ("route", models.ForeignKey(to="logistics.Route", on_delete=django.db.models.deletion.PROTECT, related_name="shipments", db_column="route_id")),
                ("origin_port", models.ForeignKey(to="logistics.Port", on_delete=django.db.models.deletion.PROTECT, related_name="departing_shipments", db_column="origin_port_id")),
                ("destination_port", models.ForeignKey(to="logistics.Port", on_delete=django.db.models.deletion.PROTECT, related_name="arriving_shipments", db_column="destination_port_id")),
                ("tracking_number", models.CharField(max_length=64, unique=True, db_column="tracking_number")),
                ("bill_of_lading", models.CharField(max_length=64, blank=True, db_column="bill_of_lading")),
                ("house_bill_of_lading", models.CharField(max_length=64, blank=True, db_column="house_bill_of_lading")),
                ("purchase_order_refs", django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=64), default=list, blank=True, db_column="purchase_order_refs")),
                ("status", models.CharField(max_length=16, choices=[("DRAFT","Draft"),("BOOKED","Booked"),("IN_TRANSIT","In Transit"),("AT_PORT","At Port"),("CUSTOMS_HOLD","Customs Hold"),("DELIVERED","Delivered"),("CANCELLED","Cancelled"),("EXCEPTION","Exception")], default="DRAFT", db_column="status")),
                ("booking_date", models.DateField(null=True, blank=True, db_column="booking_date")),
                ("departure_date", models.DateTimeField(null=True, blank=True, db_column="departure_date")),
                ("estimated_arrival", models.DateTimeField(null=True, blank=True, db_column="estimated_arrival")),
                ("actual_arrival", models.DateTimeField(null=True, blank=True, db_column="actual_arrival")),
                ("declared_value", models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, db_column="declared_value")),
                ("freight_cost", models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, db_column="freight_cost")),
                ("currency_code", models.CharField(max_length=3, default="USD", db_column="currency_code")),
                ("incoterms", models.CharField(max_length=3, blank=True, db_column="incoterms")),
                ("hs_codes", django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=10), default=list, blank=True, db_column="hs_codes")),
                ("tags", django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=64), default=list, blank=True, db_column="tags")),
                ("custom_attributes", models.JSONField(default=dict, blank=True, db_column="custom_attributes")),
                ("notes", models.TextField(blank=True, db_column="notes")),
            ],
            options={"db_table": "shipments", "ordering": ["-created_at"], "verbose_name": "Shipment", "verbose_name_plural": "Shipments"},
        ),
        migrations.AddIndex(model_name="shipment", index=django.contrib.postgres.indexes.HashIndex(fields=["tracking_number"], name="shipment_tracking_hash_idx")),
        migrations.AddIndex(model_name="shipment", index=django.contrib.postgres.indexes.HashIndex(fields=["bill_of_lading"], name="shipment_bol_hash_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["carrier_id", "status"], name="shipment_carrier_status_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["shipper_id", "status", "estimated_arrival"], name="shipment_shipper_status_eta_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["departure_date"], name="shipment_departure_date_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["estimated_arrival"], name="shipment_estimated_arrival_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["carrier_id", "estimated_arrival"], condition=models.Q(status__in=["BOOKED","IN_TRANSIT","AT_PORT"]), name="shipment_active_carr_eta_idx")),
        migrations.AddIndex(model_name="shipment", index=models.Index(fields=["created_at"], condition=models.Q(status__in=["EXCEPTION","CUSTOMS_HOLD"]), name="shipment_exception_idx")),
        migrations.AddIndex(model_name="shipment", index=django.contrib.postgres.indexes.GinIndex(fields=["custom_attributes"], name="shipment_custom_attr_gin_idx")),
        migrations.AddIndex(model_name="shipment", index=django.contrib.postgres.indexes.GinIndex(fields=["hs_codes"], name="shipment_hs_codes_gin_idx")),
        migrations.AddIndex(model_name="shipment", index=django.contrib.postgres.indexes.GinIndex(fields=["tags"], name="shipment_tags_gin_idx")),
        migrations.AddIndex(model_name="shipment", index=django.contrib.postgres.indexes.BrinIndex(fields=["created_at"], name="shipment_created_at_brin_idx", pages_per_range=128)),
        migrations.AddConstraint(model_name="shipment", constraint=models.CheckConstraint(condition=models.Q(status__in=["DRAFT","BOOKED","IN_TRANSIT","AT_PORT","CUSTOMS_HOLD","DELIVERED","CANCELLED","EXCEPTION"]), name="shipment_status_valid")),
        migrations.AddConstraint(model_name="shipment", constraint=models.CheckConstraint(condition=models.Q(declared_value__isnull=True) | models.Q(declared_value__gte=Decimal("0")), name="shipment_declared_value_non_negative")),
        migrations.AddConstraint(model_name="shipment", constraint=models.CheckConstraint(condition=models.Q(freight_cost__isnull=True) | models.Q(freight_cost__gte=Decimal("0")), name="shipment_freight_cost_non_negative")),
        migrations.AddConstraint(model_name="shipment", constraint=models.CheckConstraint(condition=models.Q(estimated_arrival__isnull=True) | models.Q(departure_date__isnull=True) | models.Q(estimated_arrival__gt=models.F("departure_date")), name="shipment_arrival_after_departure")),
        migrations.AddConstraint(model_name="shipment", constraint=models.CheckConstraint(condition=models.Q(actual_arrival__isnull=True) | models.Q(departure_date__isnull=True) | models.Q(actual_arrival__gte=models.F("departure_date")), name="shipment_actual_arrival_after_departure")),
        migrations.AddConstraint(model_name="shipment", constraint=models.CheckConstraint(condition=models.Q(currency_code__regex=r"^[A-Z]{3}$"), name="shipment_currency_code_iso4217")),
        migrations.AddConstraint(model_name="shipment", constraint=models.UniqueConstraint(fields=["bill_of_lading"], condition=~models.Q(bill_of_lading=""), name="shipment_bol_unique_nonempty")),
 
        # ── cargo ─────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Cargo",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
                ("shipment", models.ForeignKey(to="logistics.Shipment", on_delete=django.db.models.deletion.CASCADE, related_name="cargo_items", db_column="shipment_id")),
                ("cargo_type", models.CharField(max_length=12, choices=[("GENERAL","General Cargo"),("BULK","Bulk"),("CONTAINER","Containerized"),("BREAKBULK","Break Bulk"),("REEFER","Refrigerated"),("HAZMAT","Hazardous Material"),("OVERSIZED","Oversized / OOG"),("LIQUID","Liquid / Tanker")], db_column="cargo_type")),
                ("description", models.CharField(max_length=512, db_column="description")),
                ("container_number", models.CharField(max_length=11, blank=True, db_column="container_number")),
                ("seal_number", models.CharField(max_length=32, blank=True, db_column="seal_number")),
                ("gross_weight_kg", models.DecimalField(max_digits=12, decimal_places=3, db_column="gross_weight_kg")),
                ("net_weight_kg", models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, db_column="net_weight_kg")),
                ("volume_cbm", models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True, db_column="volume_cbm")),
                ("package_count", models.PositiveIntegerField(default=1, db_column="package_count")),
                ("package_type", models.CharField(max_length=32, blank=True, db_column="package_type")),
                ("is_hazmat", models.BooleanField(default=False, db_column="is_hazmat")),
                ("un_number", models.CharField(max_length=4, blank=True, db_column="un_number")),
                ("imdg_class", models.CharField(max_length=8, blank=True, db_column="imdg_class")),
                ("temperature_min_c", models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, db_column="temperature_min_c")),
                ("temperature_max_c", models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, db_column="temperature_max_c")),
                ("custom_attributes", models.JSONField(default=dict, blank=True, db_column="custom_attributes")),
            ],
            options={"db_table": "cargo", "verbose_name": "Cargo", "verbose_name_plural": "Cargo Items"},
        ),
        migrations.AddIndex(model_name="cargo", index=django.contrib.postgres.indexes.HashIndex(fields=["container_number"], name="cargo_container_number_hash_idx")),
        migrations.AddIndex(model_name="cargo", index=models.Index(fields=["shipment_id", "is_hazmat"], name="cargo_shipment_hazmat_idx")),
        migrations.AddIndex(model_name="cargo", index=models.Index(fields=["cargo_type"], name="cargo_type_idx")),
        migrations.AddIndex(model_name="cargo", index=models.Index(fields=["gross_weight_kg"], name="cargo_gross_weight_idx")),
        migrations.AddIndex(model_name="cargo", index=models.Index(fields=["temperature_min_c"], condition=models.Q(temperature_min_c__isnull=False), name="cargo_reefer_temp_partial_idx")),
        migrations.AddIndex(model_name="cargo", index=django.contrib.postgres.indexes.GinIndex(fields=["custom_attributes"], name="cargo_custom_attr_gin_idx")),
        migrations.AddConstraint(model_name="cargo", constraint=models.CheckConstraint(condition=models.Q(gross_weight_kg__gt=0), name="cargo_gross_weight_kg_positive")),
        migrations.AddConstraint(model_name="cargo", constraint=models.CheckConstraint(condition=models.Q(net_weight_kg__isnull=True) | models.Q(net_weight_kg__gt=0), name="cargo_net_weight_kg_positive")),
        migrations.AddConstraint(model_name="cargo", constraint=models.CheckConstraint(condition=models.Q(net_weight_kg__isnull=True) | models.Q(net_weight_kg__lte=models.F("gross_weight_kg")), name="cargo_net_weight_lte_gross")),
        migrations.AddConstraint(model_name="cargo", constraint=models.CheckConstraint(condition=models.Q(volume_cbm__isnull=True) | models.Q(volume_cbm__gt=0), name="cargo_volume_cbm_positive")),
        migrations.AddConstraint(model_name="cargo", constraint=models.CheckConstraint(condition=models.Q(package_count__gt=0), name="cargo_package_count_positive")),
        migrations.AddConstraint(model_name="cargo", constraint=models.CheckConstraint(condition=~models.Q(is_hazmat=True) | ~models.Q(un_number=""), name="cargo_hazmat_requires_un_number")),
        migrations.AddConstraint(model_name="cargo", constraint=models.CheckConstraint(condition=models.Q(temperature_min_c__isnull=True) | models.Q(temperature_max_c__isnull=True) | models.Q(temperature_max_c__gte=models.F("temperature_min_c")), name="cargo_temperature_range_ordered")),
        migrations.AddConstraint(model_name="cargo", constraint=models.CheckConstraint(condition=models.Q(un_number="") | models.Q(un_number__regex=r"^\d{4}$"), name="cargo_un_number_format")),
 
        # ── tracking_events ───────────────────────────────────────────────────
        migrations.CreateModel(
            name="TrackingEvent",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
                ("shipment", models.ForeignKey(to="logistics.Shipment", on_delete=django.db.models.deletion.CASCADE, related_name="tracking_events", db_column="shipment_id")),
                ("port", models.ForeignKey(to="logistics.Port", on_delete=django.db.models.deletion.PROTECT, related_name="tracking_events", null=True, blank=True, db_column="port_id")),
                ("supersedes", models.ForeignKey(to="self", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True, related_name="superseded_by", db_column="supersedes_id")),
                ("event_type", models.CharField(max_length=20, choices=[("DEPARTURE","Departure"),("ARRIVAL","Arrival"),("CUSTOMS_CLEARED","Customs Cleared"),("CUSTOMS_HOLD","Customs Hold"),("TRANSSHIPMENT","Transshipment"),("DELAY","Delay"),("EXCEPTION","Exception"),("DELIVERED","Delivered"),("DOC_RECEIVED","Document Received"),("VESSEL_CHANGE","Vessel Change")], db_column="event_type")),
                ("event_time", models.DateTimeField(db_column="event_time")),
                ("recorded_at", models.DateTimeField(auto_now_add=True, db_column="recorded_at")),
                ("location_description", models.CharField(max_length=255, blank=True, db_column="location_description")),
                ("description", models.TextField(blank=True, db_column="description")),
                ("is_exception", models.BooleanField(default=False, db_column="is_exception")),
                ("exception_resolved", models.BooleanField(default=False, db_column="exception_resolved")),
                ("source_system", models.CharField(max_length=64, blank=True, db_column="source_system")),
                ("raw_payload", models.JSONField(default=dict, blank=True, db_column="raw_payload")),
            ],
            options={"db_table": "tracking_events", "ordering": ["shipment_id", "-event_time"], "verbose_name": "Tracking Event", "verbose_name_plural": "Tracking Events"},
        ),
        migrations.AddIndex(model_name="trackingevent", index=models.Index(fields=["shipment_id", "event_time"], name="tracking_shipment_event_time_idx")),
        migrations.AddIndex(model_name="trackingevent", index=models.Index(fields=["port_id", "event_time"], name="tracking_port_event_time_idx")),
        migrations.AddIndex(model_name="trackingevent", index=models.Index(fields=["event_type", "event_time"], name="tracking_event_type_time_idx")),
        migrations.AddIndex(model_name="trackingevent", index=models.Index(fields=["shipment_id", "event_time"], condition=models.Q(is_exception=True) & models.Q(exception_resolved=False), name="tracking_unresolved_exc_idx")),
        migrations.AddIndex(model_name="trackingevent", index=django.contrib.postgres.indexes.BrinIndex(fields=["event_time"], name="tracking_event_time_brin_idx", pages_per_range=128)),
        migrations.AddIndex(model_name="trackingevent", index=django.contrib.postgres.indexes.BrinIndex(fields=["recorded_at"], name="tracking_recorded_at_brin_idx", pages_per_range=128)),
        migrations.AddIndex(model_name="trackingevent", index=django.contrib.postgres.indexes.GinIndex(fields=["raw_payload"], name="tracking_raw_payload_gin_idx")),
        migrations.AddConstraint(model_name="trackingevent", constraint=models.CheckConstraint(condition=models.Q(event_type__in=["DEPARTURE","ARRIVAL","CUSTOMS_CLEARED","CUSTOMS_HOLD","TRANSSHIPMENT","DELAY","EXCEPTION","DELIVERED","DOC_RECEIVED","VESSEL_CHANGE"]), name="tracking_event_type_valid")),
        migrations.AddConstraint(model_name="trackingevent", constraint=models.CheckConstraint(condition=~models.Q(exception_resolved=True) | models.Q(is_exception=True), name="tracking_resolved_requires_exception")),
 
        # ── port_calls ────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="PortCall",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
                ("route", models.ForeignKey(to="logistics.Route", on_delete=django.db.models.deletion.CASCADE, related_name="port_calls", db_column="route_id")),
                ("port", models.ForeignKey(to="logistics.Port", on_delete=django.db.models.deletion.PROTECT, related_name="port_calls", db_column="port_id")),
                ("call_sequence", models.PositiveSmallIntegerField(db_column="call_sequence")),
                ("vessel_name", models.CharField(max_length=128, blank=True, db_column="vessel_name")),
                ("voyage_number", models.CharField(max_length=32, blank=True, db_column="voyage_number")),
                ("scheduled_arrival", models.DateTimeField(null=True, blank=True, db_column="scheduled_arrival")),
                ("actual_arrival", models.DateTimeField(null=True, blank=True, db_column="actual_arrival")),
                ("scheduled_departure", models.DateTimeField(null=True, blank=True, db_column="scheduled_departure")),
                ("actual_departure", models.DateTimeField(null=True, blank=True, db_column="actual_departure")),
                ("arrival_delay_hours", models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, db_column="arrival_delay_hours")),
            ],
            options={"db_table": "port_calls", "ordering": ["route_id", "call_sequence"], "verbose_name": "Port Call", "verbose_name_plural": "Port Calls"},
        ),
        migrations.AddIndex(model_name="portcall", index=models.Index(fields=["route_id", "call_sequence"], name="portcall_route_sequence_idx")),
        migrations.AddIndex(model_name="portcall", index=models.Index(fields=["port_id", "scheduled_arrival"], name="portcall_port_sched_arr_idx")),
        migrations.AddIndex(model_name="portcall", index=models.Index(fields=["vessel_name", "voyage_number"], name="portcall_vessel_voyage_idx")),
        migrations.AddIndex(model_name="portcall", index=models.Index(fields=["arrival_delay_hours"], condition=models.Q(arrival_delay_hours__isnull=False), name="portcall_delay_hours_partial_idx")),
        migrations.AddConstraint(model_name="portcall", constraint=models.CheckConstraint(condition=models.Q(call_sequence__gte=1), name="portcall_call_sequence_positive")),
        migrations.AddConstraint(model_name="portcall", constraint=models.CheckConstraint(condition=models.Q(scheduled_departure__isnull=True) | models.Q(scheduled_arrival__isnull=True) | models.Q(scheduled_departure__gte=models.F("scheduled_arrival")), name="portcall_scheduled_departure_after_arrival")),
        migrations.AddConstraint(model_name="portcall", constraint=models.CheckConstraint(condition=models.Q(actual_departure__isnull=True) | models.Q(actual_arrival__isnull=True) | models.Q(actual_departure__gte=models.F("actual_arrival")), name="portcall_actual_departure_after_arrival")),
        migrations.AddConstraint(model_name="portcall", constraint=models.UniqueConstraint(fields=["route_id", "call_sequence"], name="portcall_route_sequence_unique")),
 
        # ── analytics_snapshots ───────────────────────────────────────────────
        migrations.CreateModel(
            name="AnalyticsSnapshot",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_column="id")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_column="created_at")),
                ("updated_at", models.DateTimeField(auto_now=True, db_column="updated_at")),
                ("entity_type", models.CharField(max_length=12, choices=[("CARRIER","Carrier"),("ROUTE","Route"),("PORT","Port"),("SHIPPER","Shipper"),("CONSIGNEE","Consignee"),("GLOBAL","Global")], db_column="entity_type")),
                ("entity_id", models.UUIDField(null=True, blank=True, db_column="entity_id")),
                ("snapshot_date", models.DateField(db_column="snapshot_date")),
                ("granularity", models.CharField(max_length=8, choices=[("DAILY","Daily"),("WEEKLY","Weekly"),("MONTHLY","Monthly")], db_column="granularity")),
                ("metric_key", models.CharField(max_length=128, db_column="metric_key")),
                ("metric_value", models.DecimalField(max_digits=20, decimal_places=6, db_column="metric_value")),
                ("dimensions", models.JSONField(default=dict, blank=True, db_column="dimensions")),
            ],
            options={"db_table": "analytics_snapshots", "ordering": ["-snapshot_date", "entity_type", "metric_key"], "verbose_name": "Analytics Snapshot", "verbose_name_plural": "Analytics Snapshots"},
        ),
        migrations.AddIndex(model_name="analyticssnapshot", index=models.Index(fields=["entity_type", "entity_id", "metric_key", "snapshot_date"], name="snapshot_entity_metric_date_idx")),
        migrations.AddIndex(model_name="analyticssnapshot", index=models.Index(fields=["snapshot_date", "granularity", "entity_type"], name="snapshot_date_granularity_idx")),
        migrations.AddIndex(model_name="analyticssnapshot", index=django.contrib.postgres.indexes.GinIndex(fields=["dimensions"], name="snapshot_dimensions_gin_idx")),
        migrations.AddConstraint(model_name="analyticssnapshot", constraint=models.UniqueConstraint(fields=["entity_type", "entity_id", "metric_key", "snapshot_date", "granularity"], name="snapshot_entity_metric_date_granularity_unique")),
        migrations.AddConstraint(model_name="analyticssnapshot", constraint=models.CheckConstraint(condition=models.Q(entity_type__in=["CARRIER","ROUTE","PORT","SHIPPER","CONSIGNEE","GLOBAL"]), name="snapshot_entity_type_valid")),
        migrations.AddConstraint(model_name="analyticssnapshot", constraint=models.CheckConstraint(condition=models.Q(granularity__in=["DAILY","WEEKLY","MONTHLY"]), name="snapshot_granularity_valid")),
    ]
from django.db import models


class FuelStation(models.Model):
    """A fuel station loaded from the OPIS price CSV."""

    opis_id = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=10, db_index=True)
    rack_id = models.CharField(max_length=32, blank=True)
    retail_price = models.DecimalField(max_digits=6, decimal_places=4)
    latitude = models.FloatField(null=True, blank=True, db_index=True)
    longitude = models.FloatField(null=True, blank=True, db_index=True)
    geocoded_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['city', 'state', 'name']
        indexes = [
            models.Index(fields=['state', 'city']),
            models.Index(fields=['latitude', 'longitude']),
        ]

    def __str__(self):
        return f'{self.name} - {self.city}, {self.state}'

    @property
    def location(self):
        return (self.latitude, self.longitude)

    @property
    def is_geocoded(self):
        return self.latitude is not None and self.longitude is not None


class GeocodeCache(models.Model):
    """Cache for free geocoding lookups (Nominatim) to avoid repeated calls."""

    query = models.CharField(max_length=255, unique=True, db_index=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    source = models.CharField(max_length=32, default='nominatim')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.query


class RouteCache(models.Model):
    """Cache for OSRM route responses to minimize external map API calls."""

    start_query = models.CharField(max_length=255)
    finish_query = models.CharField(max_length=255)
    start_lat = models.FloatField()
    start_lon = models.FloatField()
    finish_lat = models.FloatField()
    finish_lon = models.FloatField()
    distance_miles = models.FloatField()
    duration_seconds = models.FloatField()
    geometry = models.JSONField()  # list of [lat, lon] pairs
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['start_query', 'finish_query']]

    def __str__(self):
        return f'{self.start_query} -> {self.finish_query}'

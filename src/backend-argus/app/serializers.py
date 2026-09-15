"""Document -> response-schema conversion. The one place ER-style `id_x` names get populated
from Beanie's `doc.id`, and the one place (besides `app/geo.py` itself) that calls
`to_latlon()` — kept together here per resource rather than duplicated across routers.
"""
from __future__ import annotations

from typing import Optional

from app.geo import to_latlon
from app.models.alert import Alert
from app.models.driver import Driver
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.models.truck import Truck
from app.models.user import User
from app.schemas.alert import AlertOut
from app.schemas.driver import DriverOut
from app.schemas.route import RouteOut, RouteWithStatus
from app.schemas.status_route import StatusRouteOut
from app.schemas.truck import TruckOut
from app.schemas.user import UserOut


def user_out(doc: User) -> UserOut:
    return UserOut(
        id_user=str(doc.id),
        email=doc.email,
        role=doc.role,
        first_name=doc.first_name,
        last_name=doc.last_name,
        phone_number=doc.phone_number,
        is_active=doc.is_active,
        last_login=doc.last_login,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def truck_out(doc: Truck) -> TruckOut:
    return TruckOut(
        id_truck=str(doc.id),
        plate_number=doc.plate_number,
        brand=doc.brand,
        model=doc.model,
        company_number=doc.company_number,
        raspberry_pi_mac=doc.raspberry_pi_mac,
        esp32_id=doc.esp32_id,
        operative_status=doc.operative_status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def driver_out(doc: Driver) -> DriverOut:
    return DriverOut(
        id_driver=str(doc.id),
        first_name=doc.first_name,
        last_name=doc.last_name,
        license_number=doc.license_number,
        license_expiration=doc.license_expiration,
        phone_number=doc.phone_number,
        emergency_contact_name=doc.emergency_contact_name,
        emergency_contact_phone=doc.emergency_contact_phone,
        blood_type=doc.blood_type,
        operative_status=doc.operative_status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def route_out(doc: Route) -> RouteOut:
    return RouteOut(
        id_route=str(doc.id),
        id_driver=doc.id_driver,
        id_truck=doc.id_truck,
        origin_name=doc.origin_name,
        destination_name=doc.destination_name,
        destination_coordinates=to_latlon(doc.destination_coordinates),
        estimated_departure=doc.estimated_departure,
        estimated_arrival=doc.estimated_arrival,
        actual_departure=doc.actual_departure,
        actual_arrival=doc.actual_arrival,
        operative_status=doc.operative_status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def status_route_out(doc: StatusRoute) -> StatusRouteOut:
    return StatusRouteOut(
        id_status_route=str(doc.id),
        id_route=doc.id_route,
        current_coordinates=to_latlon(doc.current_coordinates),
        current_speed=doc.current_speed,
        odometer=doc.odometer,
        vigilance=doc.vigilance,
        timestamp=doc.timestamp,
    )


def route_with_status_out(
    route: Route,
    latest_status: Optional[StatusRoute],
    truck: Optional[Truck],
    driver: Optional[Driver],
) -> RouteWithStatus:
    base = route_out(route)
    return RouteWithStatus(
        **base.model_dump(),
        latest_status=status_route_out(latest_status) if latest_status else None,
        truck_plate_number=truck.plate_number if truck else None,
        driver_full_name=f"{driver.first_name} {driver.last_name}" if driver else None,
    )


def alert_out(doc: Alert) -> AlertOut:
    return AlertOut(
        id_alert=str(doc.id),
        id_route=doc.id_route,
        alert_type=doc.alert_type,
        severity_level=doc.severity_level,
        source=doc.source,
        ai_metadata=doc.ai_metadata,
        grip_status=doc.grip_status,
        related_alert_id=doc.related_alert_id,
        resolved_at=doc.resolved_at,
        media_url=doc.media_url,
        coordinates=to_latlon(doc.coordinates),
        speed_at_event=doc.speed_at_event,
        timestamp=doc.timestamp,
        reviewed_by_operator=doc.reviewed_by_operator,
        operator_notes=doc.operator_notes,
    )

"""
DJI DroneID Packet Analyzer

Parses and analyzes decoded DJI DroneID packets to extract:
- Drone GPS position and velocity
- Pilot/operator location (from smartphone GPS)
- Serial number and device identification
- Home point (takeoff location)
- Flight path reconstruction

Works with output from RUB-SysSec/DroneSecurity decoder or similar tools.

Usage:
    python3 droneid_analyzer.py --file decoded_packets.json --analyze
    python3 droneid_analyzer.py --file decoded_packets.json --track
    python3 droneid_analyzer.py --demo

For authorized security research only.
"""

import argparse
import json
import math
import sys
import time


# DroneID packet field definitions
DRONEID_FIELDS = {
    'serial_number': {'offset': 0, 'type': 'string', 'desc': 'Drone serial number'},
    'device_type': {'offset': None, 'type': 'uint8', 'desc': 'Device type code'},
    'uuid': {'offset': None, 'type': 'bytes', 'desc': 'Unique identifier'},
    'uuid_len': {'offset': None, 'type': 'uint8', 'desc': 'UUID length'},
    'latitude': {'offset': None, 'type': 'float64', 'desc': 'Drone latitude (degrees)'},
    'longitude': {'offset': None, 'type': 'float64', 'desc': 'Drone longitude (degrees)'},
    'altitude': {'offset': None, 'type': 'float32', 'desc': 'Drone altitude (m MSL)'},
    'height': {'offset': None, 'type': 'float32', 'desc': 'Drone height above takeoff (m)'},
    'v_north': {'offset': None, 'type': 'float32', 'desc': 'Velocity north (m/s)'},
    'v_east': {'offset': None, 'type': 'float32', 'desc': 'Velocity east (m/s)'},
    'v_up': {'offset': None, 'type': 'float32', 'desc': 'Velocity up (m/s)'},
    'latitude_home': {'offset': None, 'type': 'float64', 'desc': 'Home point latitude'},
    'longitude_home': {'offset': None, 'type': 'float64', 'desc': 'Home point longitude'},
    'app_lat': {'offset': None, 'type': 'float64', 'desc': 'Pilot/app latitude'},
    'app_lon': {'offset': None, 'type': 'float64', 'desc': 'Pilot/app longitude'},
    'gps_time': {'offset': None, 'type': 'uint64', 'desc': 'GPS timestamp'},
    'sequence_number': {'offset': None, 'type': 'uint16', 'desc': 'Packet sequence'},
    'state_info': {'offset': None, 'type': 'uint8', 'desc': 'State information'},
    'version': {'offset': None, 'type': 'uint8', 'desc': 'Protocol version'},
}

# DJI device type codes (known)
DEVICE_TYPES = {
    0: 'Unknown',
    1: 'Mavic Mini / Mini SE',
    2: 'Mavic Air',
    3: 'Mavic Air 2',
    4: 'Mavic 2 Pro/Zoom',
    5: 'Mavic 3',
    10: 'Mini 2',
    11: 'Mini 3 / Mini 3 Pro',
    14: 'Air 2S',
    15: 'Avata',
    16: 'Avata 2',
    20: 'FPV',
    30: 'Inspire 2',
    40: 'Matrice 300 RTK',
    41: 'Matrice 30',
    50: 'Phantom 4 Pro',
    51: 'Phantom 4 RTK',
    60: 'Agras T30',
}


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two GPS coordinates in meters."""
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def bearing(lat1, lon1, lat2, lon2):
    """Calculate bearing between two GPS coordinates in degrees."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)

    brng = math.degrees(math.atan2(x, y))
    return (brng + 360) % 360


class DroneIDPacket:
    """Represents a decoded DJI DroneID packet."""

    def __init__(self, data=None):
        self.serial_number = None
        self.device_type = 0
        self.uuid = None
        self.latitude = 0.0
        self.longitude = 0.0
        self.altitude = 0.0
        self.height = 0.0
        self.v_north = 0.0
        self.v_east = 0.0
        self.v_up = 0.0
        self.latitude_home = 0.0
        self.longitude_home = 0.0
        self.app_lat = 0.0
        self.app_lon = 0.0
        self.gps_time = 0
        self.sequence_number = 0
        self.state_info = 0
        self.version = 0
        self.crc_valid = False
        self.raw_data = data

        if isinstance(data, dict):
            self._from_dict(data)

    def _from_dict(self, d):
        """Load fields from a dictionary (JSON decoded output)."""
        for key in DRONEID_FIELDS:
            if key in d:
                setattr(self, key, d[key])
        self.crc_valid = d.get('crc_valid', d.get('crc_ok', False))

    @property
    def speed_ground(self):
        """Calculate ground speed in m/s."""
        return math.sqrt(self.v_north ** 2 + self.v_east ** 2)

    @property
    def speed_3d(self):
        """Calculate 3D speed in m/s."""
        return math.sqrt(self.v_north ** 2 + self.v_east ** 2 + self.v_up ** 2)

    @property
    def heading(self):
        """Calculate heading from velocity vector."""
        if self.v_north == 0 and self.v_east == 0:
            return 0.0
        return (math.degrees(math.atan2(self.v_east, self.v_north)) + 360) % 360

    @property
    def device_type_name(self):
        return DEVICE_TYPES.get(self.device_type, f'Unknown ({self.device_type})')

    @property
    def pilot_distance(self):
        """Distance from drone to pilot in meters."""
        if self.app_lat == 0 and self.app_lon == 0:
            return None
        return haversine_distance(self.latitude, self.longitude, self.app_lat, self.app_lon)

    @property
    def home_distance(self):
        """Distance from drone to home point in meters."""
        if self.latitude_home == 0 and self.longitude_home == 0:
            return None
        return haversine_distance(self.latitude, self.longitude,
                                  self.latitude_home, self.longitude_home)

    def describe(self):
        """Return a human-readable description."""
        lines = [
            f"DroneID Packet (seq={self.sequence_number}, CRC={'OK' if self.crc_valid else 'FAIL'})",
            f"  Serial:     {self.serial_number or 'N/A'}",
            f"  Device:     {self.device_type_name}",
            f"  Position:   {self.latitude:.7f}, {self.longitude:.7f}  alt={self.altitude:.1f}m  height={self.height:.1f}m",
            f"  Velocity:   N={self.v_north:.1f} E={self.v_east:.1f} Up={self.v_up:.1f}  "
            f"(ground={self.speed_ground:.1f} m/s, hdg={self.heading:.0f}°)",
            f"  Home:       {self.latitude_home:.7f}, {self.longitude_home:.7f}",
            f"  Pilot/App:  {self.app_lat:.7f}, {self.app_lon:.7f}",
        ]

        if self.pilot_distance is not None:
            lines.append(f"  Pilot dist: {self.pilot_distance:.0f}m")
        if self.home_distance is not None:
            lines.append(f"  Home dist:  {self.home_distance:.0f}m")

        return '\n'.join(lines)


class DroneTracker:
    """Tracks multiple drones from DroneID packets over time."""

    def __init__(self):
        self.drones = {}  # serial -> list of packets
        self.packet_count = 0
        self.valid_count = 0

    def add_packet(self, pkt):
        """Add a decoded DroneID packet to the tracker."""
        self.packet_count += 1
        if pkt.crc_valid:
            self.valid_count += 1

        key = pkt.serial_number or f'unknown_{pkt.device_type}'
        if key not in self.drones:
            self.drones[key] = []
        self.drones[key].append(pkt)

    def load_json(self, filepath):
        """Load decoded packets from a JSON file."""
        with open(filepath, 'r') as f:
            data = json.load(f)

        if isinstance(data, list):
            for entry in data:
                pkt = DroneIDPacket(entry)
                self.add_packet(pkt)
        elif isinstance(data, dict) and 'packets' in data:
            for entry in data['packets']:
                pkt = DroneIDPacket(entry)
                self.add_packet(pkt)

    def get_drone_summary(self, serial):
        """Get summary for a tracked drone."""
        if serial not in self.drones:
            return None

        packets = self.drones[serial]
        if not packets:
            return None

        first = packets[0]
        last = packets[-1]

        # Calculate flight path stats
        total_distance = 0
        max_alt = 0
        max_speed = 0
        max_height = 0

        for i, pkt in enumerate(packets):
            if pkt.altitude > max_alt:
                max_alt = pkt.altitude
            if pkt.height > max_height:
                max_height = pkt.height
            if pkt.speed_ground > max_speed:
                max_speed = pkt.speed_ground

            if i > 0:
                prev = packets[i - 1]
                if prev.latitude != 0 and pkt.latitude != 0:
                    d = haversine_distance(prev.latitude, prev.longitude,
                                           pkt.latitude, pkt.longitude)
                    total_distance += d

        return {
            'serial': serial,
            'device_type': first.device_type_name,
            'packets': len(packets),
            'first_seen': first.sequence_number,
            'last_seen': last.sequence_number,
            'last_position': (last.latitude, last.longitude, last.altitude),
            'home_point': (first.latitude_home, first.longitude_home),
            'pilot_location': (last.app_lat, last.app_lon),
            'max_altitude': max_alt,
            'max_height': max_height,
            'max_speed': max_speed,
            'total_distance': total_distance,
        }

    def print_all_summaries(self):
        """Print summaries for all tracked drones."""
        print(f"\n{'=' * 70}")
        print(f"DRONE TRACKING SUMMARY — {len(self.drones)} drone(s), "
              f"{self.packet_count} packets ({self.valid_count} valid)")
        print(f"{'=' * 70}")

        for serial in sorted(self.drones.keys()):
            summary = self.get_drone_summary(serial)
            if summary is None:
                continue

            print(f"\n--- {serial} ({summary['device_type']}) ---")
            print(f"  Packets:        {summary['packets']}")
            print(f"  Seq range:      {summary['first_seen']} → {summary['last_seen']}")
            lat, lon, alt = summary['last_position']
            print(f"  Last position:  {lat:.7f}, {lon:.7f}  alt={alt:.1f}m")
            hlat, hlon = summary['home_point']
            print(f"  Home point:     {hlat:.7f}, {hlon:.7f}")
            plat, plon = summary['pilot_location']
            print(f"  Pilot location: {plat:.7f}, {plon:.7f}")

            if plat != 0 and plon != 0:
                pilot_dist = haversine_distance(lat, lon, plat, plon)
                pilot_brg = bearing(lat, lon, plat, plon)
                print(f"  Pilot bearing:  {pilot_brg:.0f}° at {pilot_dist:.0f}m from drone")

            print(f"  Max altitude:   {summary['max_altitude']:.1f}m MSL")
            print(f"  Max height:     {summary['max_height']:.1f}m AGL")
            print(f"  Max speed:      {summary['max_speed']:.1f} m/s ({summary['max_speed'] * 3.6:.1f} km/h)")
            print(f"  Total distance: {summary['total_distance']:.0f}m ({summary['total_distance'] / 1000:.2f}km)")

    def export_kml(self, filepath, serial=None):
        """Export flight path(s) as KML for Google Earth."""
        drones_to_export = [serial] if serial else list(self.drones.keys())

        kml_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<kml xmlns="http://www.opengis.net/kml/2.2">',
            '<Document>',
            '<name>DroneID Flight Tracks</name>',
        ]

        for s in drones_to_export:
            if s not in self.drones:
                continue

            packets = self.drones[s]
            coords = []
            for pkt in packets:
                if pkt.latitude != 0 and pkt.longitude != 0:
                    coords.append(f"{pkt.longitude},{pkt.latitude},{pkt.altitude}")

            if not coords:
                continue

            kml_parts.extend([
                '<Placemark>',
                f'<name>Drone: {s}</name>',
                '<Style><LineStyle><color>ff0000ff</color><width>3</width></LineStyle></Style>',
                '<LineString>',
                '<altitudeMode>absolute</altitudeMode>',
                f'<coordinates>{" ".join(coords)}</coordinates>',
                '</LineString>',
                '</Placemark>',
            ])

            # Mark pilot location
            last = packets[-1]
            if last.app_lat != 0 and last.app_lon != 0:
                kml_parts.extend([
                    '<Placemark>',
                    f'<name>Pilot: {s}</name>',
                    '<Style><IconStyle><color>ff00ff00</color></IconStyle></Style>',
                    f'<Point><coordinates>{last.app_lon},{last.app_lat},0</coordinates></Point>',
                    '</Placemark>',
                ])

            # Mark home point
            if last.latitude_home != 0 and last.longitude_home != 0:
                kml_parts.extend([
                    '<Placemark>',
                    f'<name>Home: {s}</name>',
                    '<Style><IconStyle><color>ffff0000</color></IconStyle></Style>',
                    f'<Point><coordinates>{last.longitude_home},{last.latitude_home},0</coordinates></Point>',
                    '</Placemark>',
                ])

        kml_parts.extend(['</Document>', '</kml>'])

        with open(filepath, 'w') as f:
            f.write('\n'.join(kml_parts))
        print(f"[+] KML exported to {filepath}")

    def export_csv(self, filepath, serial=None):
        """Export packet data as CSV."""
        drones_to_export = [serial] if serial else list(self.drones.keys())

        with open(filepath, 'w') as f:
            f.write("serial,seq,lat,lon,alt,height,v_north,v_east,v_up,"
                    "speed_ground,heading,home_lat,home_lon,pilot_lat,pilot_lon,"
                    "pilot_dist,gps_time\n")

            for s in drones_to_export:
                if s not in self.drones:
                    continue
                for pkt in self.drones[s]:
                    pd = pkt.pilot_distance or 0
                    f.write(f"{pkt.serial_number},{pkt.sequence_number},"
                            f"{pkt.latitude:.7f},{pkt.longitude:.7f},"
                            f"{pkt.altitude:.1f},{pkt.height:.1f},"
                            f"{pkt.v_north:.2f},{pkt.v_east:.2f},{pkt.v_up:.2f},"
                            f"{pkt.speed_ground:.2f},{pkt.heading:.1f},"
                            f"{pkt.latitude_home:.7f},{pkt.longitude_home:.7f},"
                            f"{pkt.app_lat:.7f},{pkt.app_lon:.7f},"
                            f"{pd:.0f},{pkt.gps_time}\n")

        print(f"[+] CSV exported to {filepath}")


def demo():
    """Demonstrate with synthetic DroneID packet data."""
    print("DJI DroneID Analyzer — Demo")
    print("=" * 50)

    # Simulate a sequence of decoded DroneID packets
    demo_packets = []
    base_lat = 51.4416
    base_lon = 6.7588
    base_alt = 45.0

    for i in range(20):
        pkt_data = {
            'serial_number': 'DEMO123456789',
            'device_type': 10,  # Mini 2
            'uuid': 'abcdef1234567890',
            'latitude': base_lat + i * 0.0001,
            'longitude': base_lon + i * 0.00005,
            'altitude': base_alt + i * 2,
            'height': 10.0 + i * 2,
            'v_north': 3.0 + i * 0.1,
            'v_east': 1.5,
            'v_up': 0.5 if i < 10 else -0.3,
            'latitude_home': base_lat,
            'longitude_home': base_lon,
            'app_lat': base_lat - 0.001,
            'app_lon': base_lon + 0.002,
            'gps_time': 1698700000 + i * 2,
            'sequence_number': i,
            'state_info': 0,
            'version': 2,
            'crc_valid': True,
        }
        demo_packets.append(pkt_data)

    tracker = DroneTracker()
    for pkt_data in demo_packets:
        pkt = DroneIDPacket(pkt_data)
        tracker.add_packet(pkt)
        if pkt.sequence_number in (0, 10, 19):
            print(f"\n{pkt.describe()}")

    tracker.print_all_summaries()

    print("\n[*] Note: This demo uses synthetic data for illustration.")
    print("    Real analysis requires decoded DroneID packets from")
    print("    RUB-SysSec/DroneSecurity or proto17/dji_droneid tools.")


def main():
    parser = argparse.ArgumentParser(description='DJI DroneID Packet Analyzer')
    parser.add_argument('--file', '-f', help='JSON file with decoded DroneID packets')
    parser.add_argument('--analyze', '-a', action='store_true', help='Full analysis')
    parser.add_argument('--track', '-t', action='store_true', help='Track drones')
    parser.add_argument('--kml', help='Export flight paths to KML file')
    parser.add_argument('--csv', help='Export packet data to CSV file')
    parser.add_argument('--serial', '-s', help='Filter by serial number')
    parser.add_argument('--demo', action='store_true', help='Run demo with sample data')

    args = parser.parse_args()

    if args.demo:
        demo()
        return

    if not args.file:
        parser.print_help()
        return

    tracker = DroneTracker()
    tracker.load_json(args.file)

    if args.analyze or args.track:
        tracker.print_all_summaries()

    if args.kml:
        tracker.export_kml(args.kml, serial=args.serial)

    if args.csv:
        tracker.export_csv(args.csv, serial=args.serial)

    if not args.analyze and not args.track and not args.kml and not args.csv:
        tracker.print_all_summaries()


if __name__ == '__main__':
    main()

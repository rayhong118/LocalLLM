import React, { useEffect, useRef, useState, useMemo } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import {
  MapPin,
  Layers,
  RotateCcw,
  Maximize2,
  Minimize2,
  ExternalLink,
  X
} from 'lucide-react';

export interface WsdotMapAlertItem {
  id: string | number;
  road: string;
  highway: string;
  priority: string;
  category: string;
  headline: string;
  direction: string;
  milepost: string;
  start: { lat: number; lon: number };
  end: { lat: number; lon: number } | null;
  startTime: string;
  endTime: string;
}

interface WsdotMapProps {
  alerts: WsdotMapAlertItem[];
}

export const WsdotMap: React.FC<WsdotMapProps> = ({ alerts }) => {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<L.Map | null>(null);
  const layerGroupRef = useRef<L.LayerGroup | null>(null);
  const tileLayerRef = useRef<L.TileLayer | null>(null);

  const [activeCorridor, setActiveCorridor] = useState<string>('ALL');
  const [activePriority, setActivePriority] = useState<string>('ALL');
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const [selectedAlert, setSelectedAlert] = useState<WsdotMapAlertItem | null>(null);

  // Available corridors in current alert data
  const corridorList = useMemo(() => {
    const set = new Set<string>();
    alerts.forEach((a) => {
      if (a.highway) set.add(a.highway);
    });
    return Array.from(set).sort();
  }, [alerts]);

  // Filtered alerts based on selected corridor and priority
  const filteredAlerts = useMemo(() => {
    return alerts.filter((a) => {
      if (activeCorridor !== 'ALL' && a.highway !== activeCorridor) return false;
      if (activePriority !== 'ALL') {
        const p = a.priority.toUpperCase();
        if (activePriority === 'HIGH' && p !== 'HIGH' && p !== 'HIGHEST') return false;
        if (activePriority === 'MEDIUM' && p !== 'MEDIUM') return false;
        if (activePriority === 'LOW' && p !== 'LOW' && p !== 'LOWEST') return false;
      }
      return true;
    });
  }, [alerts, activeCorridor, activePriority]);

  // Priority badge & color helper
  const getPriorityTheme = (prio: string, category: string) => {
    const p = prio.toUpperCase();
    const c = category.toLowerCase();
    if (p === 'HIGHEST' || p === 'HIGH' || c.includes('closure')) {
      return {
        bg: '#ef4444',
        border: '#b91c1c',
        glow: 'rgba(239, 68, 68, 0.4)',
        label: 'High Impact',
        textColor: '#ffffff'
      };
    }
    if (p === 'MEDIUM') {
      return {
        bg: '#f59e0b',
        border: '#d97706',
        glow: 'rgba(245, 158, 11, 0.35)',
        label: 'Medium Impact',
        textColor: '#ffffff'
      };
    }
    return {
      bg: '#0284c7',
      border: '#0369a1',
      glow: 'rgba(2, 132, 199, 0.25)',
      label: 'Minor / Info',
      textColor: '#ffffff'
    };
  };

  // Initialize Leaflet Map
  useEffect(() => {
    if (!mapContainerRef.current) return;

    if (!mapInstanceRef.current) {
      const map = L.map(mapContainerRef.current, {
        center: [47.6101, -122.2015], // Bellevue / Seattle Center
        zoom: 11,
        zoomControl: true,
        scrollWheelZoom: false
      });

      // Standard OpenStreetMap public tiles (100% free, no API key required)
      const tiles = L.tileLayer(
        'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        {
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors',
          maxZoom: 19
        }
      ).addTo(map);

      tileLayerRef.current = tiles;
      layerGroupRef.current = L.layerGroup().addTo(map);
      mapInstanceRef.current = map;
    }

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
        layerGroupRef.current = null;
      }
    };
  }, []);

  // Invalidate map size when expanded
  useEffect(() => {
    if (mapInstanceRef.current) {
      setTimeout(() => {
        mapInstanceRef.current?.invalidateSize();
      }, 250);
    }
  }, [isExpanded]);

  // Update Markers & Lines when filteredAlerts change
  useEffect(() => {
    const map = mapInstanceRef.current;
    const layerGroup = layerGroupRef.current;
    if (!map || !layerGroup) return;

    layerGroup.clearLayers();

    if (filteredAlerts.length === 0) return;

    const bounds = L.latLngBounds([]);

    filteredAlerts.forEach((alert) => {
      const theme = getPriorityTheme(alert.priority, alert.category);
      const isHigh = alert.priority.toUpperCase() === 'HIGH' || alert.priority.toUpperCase() === 'HIGHEST' || alert.category.toLowerCase().includes('closure');

      // Create Custom HTML Pin Icon
      const iconHtml = `
        <div style="
          position: relative;
          display: flex;
          align-items: center;
          justify-content: center;
          width: 32px;
          height: 32px;
        ">
          ${isHigh ? `<div style="
            position: absolute;
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: ${theme.glow};
            animation: pulse-ring 2s infinite ease-out;
          "></div>` : ''}
          <div style="
            position: relative;
            z-index: 2;
            width: 26px;
            height: 26px;
            border-radius: 50%;
            background: ${theme.bg};
            border: 2px solid #ffffff;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            color: #ffffff;
            font-size: 11px;
            font-weight: 700;
            cursor: pointer;
            transition: transform 0.15s ease;
          ">
            ${alert.highway.replace('I-', '').replace('SR ', '')}
          </div>
        </div>
      `;

      const customIcon = L.divIcon({
        className: 'custom-wsdot-pin',
        html: iconHtml,
        iconSize: [32, 32],
        iconAnchor: [16, 16]
      });

      const startCoord = L.latLng(alert.start.lat, alert.start.lon);
      bounds.extend(startCoord);

      // Marker popup content
      const popupContent = `
        <div style="font-family: inherit; font-size: 12px; line-height: 1.4; color: #1e293b; min-width: 200px; max-width: 280px; padding: 2px;">
          <div style="display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 6px;">
            <span style="font-weight: 700; font-size: 13px; color: #0f172a;">${alert.highway} ${alert.direction ? alert.direction : ''}</span>
            <span style="
              font-size: 10px;
              font-weight: 600;
              padding: 2px 6px;
              border-radius: 999px;
              background-color: ${theme.bg};
              color: #ffffff;
            ">${alert.priority}</span>
          </div>
          ${alert.milepost ? `<div style="color: #64748b; font-size: 11px; margin-bottom: 4px; font-weight: 600;">📍 ${alert.milepost}</div>` : ''}
          <div style="font-weight: 600; margin-bottom: 6px; color: #1e293b;">${alert.headline}</div>
          <div style="font-size: 11px; color: #64748b; margin-bottom: 8px;">⏱️ ${alert.startTime} ➔ ${alert.endTime}</div>
          <a href="https://www.google.com/maps/search/?api=1&query=${alert.start.lat},${alert.start.lon}" target="_blank" rel="noopener noreferrer" style="
            display: inline-flex;
            align-items: center;
            gap: 4px;
            color: #f97316;
            font-size: 11px;
            font-weight: 600;
            text-decoration: none;
          ">
            Open in Google Maps ↗
          </a>
        </div>
      `;

      const marker = L.marker(startCoord, { icon: customIcon });
      marker.bindPopup(popupContent, {
        closeButton: true,
        autoClose: false
      });

      marker.on('click', () => {
        setSelectedAlert(alert);
      });

      layerGroup.addLayer(marker);

      // If valid end coordinates exist, draw highlighted road segment polyline
      if (alert.end && (alert.end.lat !== alert.start.lat || alert.end.lon !== alert.start.lon)) {
        const endCoord = L.latLng(alert.end.lat, alert.end.lon);
        bounds.extend(endCoord);

        const line = L.polyline([startCoord, endCoord], {
          color: theme.bg,
          weight: 5,
          opacity: 0.8,
          dashArray: isHigh ? undefined : '6, 6'
        });
        line.bindPopup(popupContent);
        layerGroup.addLayer(line);
      }
    });

    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [35, 35], maxZoom: 14 });
    }
  }, [filteredAlerts]);

  // Fit all markers handler
  const handleFitBounds = () => {
    const map = mapInstanceRef.current;
    if (!map || filteredAlerts.length === 0) return;
    const bounds = L.latLngBounds([]);
    filteredAlerts.forEach((a) => {
      bounds.extend([a.start.lat, a.start.lon]);
      if (a.end) bounds.extend([a.end.lat, a.end.lon]);
    });
    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [35, 35], maxZoom: 14 });
    }
  };

  if (!alerts || alerts.length === 0) return null;

  return (
    <div
      style={{
        marginTop: '0.75rem',
        marginBottom: '1rem',
        borderRadius: '12px',
        border: '1px solid var(--color-border)',
        backgroundColor: '#ffffff',
        overflow: 'hidden',
        boxShadow: '0 4px 14px rgba(15, 23, 42, 0.06)'
      }}
    >
      {/* Map Header Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '0.6rem',
          padding: '0.65rem 0.9rem',
          backgroundColor: '#f8fafc',
          borderBottom: '1px solid var(--color-border)'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '24px',
              height: '24px',
              borderRadius: '6px',
              backgroundColor: 'var(--color-primary-soft)',
              color: 'var(--color-primary)'
            }}
          >
            <MapPin size={14} />
          </span>
          <span style={{ fontWeight: 700, fontSize: '0.85rem', color: 'var(--color-text-main)' }}>
            WSDOT Live Highway Warning Map
          </span>
          <span
            style={{
              fontSize: '0.72rem',
              fontWeight: 600,
              padding: '0.15rem 0.5rem',
              borderRadius: '999px',
              backgroundColor: 'rgba(249, 115, 22, 0.1)',
              color: 'var(--color-primary)'
            }}
          >
            {filteredAlerts.length} {filteredAlerts.length === 1 ? 'Location' : 'Locations'}
          </span>
        </div>

        {/* Action Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <button
            type="button"
            onClick={handleFitBounds}
            title="Recenter & fit all markers"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.25rem',
              padding: '0.25rem 0.55rem',
              fontSize: '0.72rem',
              fontWeight: 600,
              borderRadius: '6px',
              border: '1px solid var(--color-border)',
              backgroundColor: '#ffffff',
              color: 'var(--color-text-body)',
              cursor: 'pointer'
            }}
          >
            <RotateCcw size={11} /> Fit Map
          </button>
          <button
            type="button"
            onClick={() => setIsExpanded(!isExpanded)}
            title={isExpanded ? 'Collapse Map' : 'Expand Map'}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.25rem',
              padding: '0.25rem 0.55rem',
              fontSize: '0.72rem',
              fontWeight: 600,
              borderRadius: '6px',
              border: '1px solid var(--color-border)',
              backgroundColor: '#ffffff',
              color: 'var(--color-text-body)',
              cursor: 'pointer'
            }}
          >
            {isExpanded ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
            {isExpanded ? 'Normal' : 'Expand'}
          </button>
        </div>
      </div>

      {/* Filter Filter Chips Bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.4rem',
          padding: '0.4rem 0.9rem',
          backgroundColor: '#f1f5f9',
          borderBottom: '1px solid var(--color-border)',
          overflowX: 'auto',
          fontSize: '0.72rem'
        }}
      >
        <span style={{ color: 'var(--color-text-muted)', fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: '0.2rem' }}>
          <Layers size={11} /> Corridor:
        </span>
        <button
          type="button"
          onClick={() => setActiveCorridor('ALL')}
          style={{
            padding: '0.15rem 0.55rem',
            borderRadius: '999px',
            border: activeCorridor === 'ALL' ? '1px solid var(--color-primary)' : '1px solid var(--color-border)',
            backgroundColor: activeCorridor === 'ALL' ? 'var(--color-primary)' : '#ffffff',
            color: activeCorridor === 'ALL' ? '#ffffff' : 'var(--color-text-body)',
            fontWeight: 600,
            cursor: 'pointer'
          }}
        >
          All ({alerts.length})
        </button>
        {corridorList.map((hwy) => {
          const count = alerts.filter((a) => a.highway === hwy).length;
          const isSelected = activeCorridor === hwy;
          return (
            <button
              key={hwy}
              type="button"
              onClick={() => setActiveCorridor(hwy)}
              style={{
                padding: '0.15rem 0.55rem',
                borderRadius: '999px',
                border: isSelected ? '1px solid var(--color-primary)' : '1px solid var(--color-border)',
                backgroundColor: isSelected ? 'var(--color-primary)' : '#ffffff',
                color: isSelected ? '#ffffff' : 'var(--color-text-body)',
                fontWeight: 600,
                cursor: 'pointer',
                whiteSpace: 'nowrap'
              }}
            >
              {hwy} ({count})
            </button>
          );
        })}

        <div style={{ height: '14px', width: '1px', backgroundColor: '#cbd5e1', margin: '0 0.2rem' }} />

        {/* Priority Filter */}
        <span style={{ color: 'var(--color-text-muted)', fontWeight: 600 }}>Priority:</span>
        {(['ALL', 'HIGH', 'MEDIUM'] as const).map((p) => {
          const isSelected = activePriority === p;
          const label = p === 'ALL' ? 'Any' : p === 'HIGH' ? '🚨 High' : '⚠️ Medium';
          return (
            <button
              key={p}
              type="button"
              onClick={() => setActivePriority(p)}
              style={{
                padding: '0.15rem 0.5rem',
                borderRadius: '999px',
                border: isSelected ? '1px solid var(--color-primary)' : '1px solid var(--color-border)',
                backgroundColor: isSelected ? 'var(--color-primary)' : '#ffffff',
                color: isSelected ? '#ffffff' : 'var(--color-text-body)',
                fontWeight: 600,
                cursor: 'pointer'
              }}
            >
              {label}
            </button>
          );
        })}
      </div>

      {/* Map Container Element */}
      <div
        ref={mapContainerRef}
        style={{
          width: '100%',
          height: isExpanded ? '520px' : '360px',
          transition: 'height 0.25s ease-in-out',
          zIndex: 1,
          backgroundColor: '#e2e8f0'
        }}
      />

      {/* Selected Alert Quick Inspector Card */}
      {selectedAlert && (
        <div
          style={{
            padding: '0.65rem 0.9rem',
            backgroundColor: '#ffffff',
            borderTop: '1px solid var(--color-border)',
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            gap: '0.8rem',
            fontSize: '0.8rem'
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.2rem' }}>
              <span style={{ fontWeight: 700, color: 'var(--color-text-main)' }}>
                {selectedAlert.highway} {selectedAlert.direction ? `(${selectedAlert.direction})` : ''}
              </span>
              {selectedAlert.milepost && (
                <span style={{ color: 'var(--color-text-muted)', fontSize: '0.72rem', fontWeight: 600 }}>
                  {selectedAlert.milepost}
                </span>
              )}
              <span
                style={{
                  fontSize: '0.68rem',
                  fontWeight: 700,
                  padding: '0.1rem 0.45rem',
                  borderRadius: '999px',
                  backgroundColor: getPriorityTheme(selectedAlert.priority, selectedAlert.category).bg,
                  color: '#ffffff'
                }}
              >
                {selectedAlert.priority}
              </span>
            </div>
            <div style={{ color: 'var(--color-text-body)', fontWeight: 600, fontSize: '0.82rem', marginBottom: '0.25rem' }}>
              {selectedAlert.headline}
            </div>
            <div style={{ color: 'var(--color-text-muted)', fontSize: '0.74rem' }}>
              ⏱️ {selectedAlert.startTime} ➔ {selectedAlert.endTime}
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <a
              href={`https://www.google.com/maps/search/?api=1&query=${selectedAlert.start.lat},${selectedAlert.start.lon}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.25rem',
                padding: '0.25rem 0.5rem',
                fontSize: '0.72rem',
                fontWeight: 600,
                borderRadius: '6px',
                border: '1px solid var(--color-primary-border)',
                backgroundColor: 'var(--color-primary-soft)',
                color: 'var(--color-primary)',
                textDecoration: 'none'
              }}
            >
              Google Maps <ExternalLink size={11} />
            </a>
            <button
              type="button"
              onClick={() => setSelectedAlert(null)}
              title="Close inspection"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '0.25rem',
                borderRadius: '6px',
                border: '1px solid var(--color-border)',
                backgroundColor: 'transparent',
                color: 'var(--color-text-muted)',
                cursor: 'pointer'
              }}
            >
              <X size={13} />
            </button>
          </div>
        </div>
      )}

      {/* Interactive Footer Legend / Quick Info Bar */}
      <div
        style={{
          padding: '0.45rem 0.9rem',
          backgroundColor: '#f8fafc',
          borderTop: '1px solid var(--color-border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '0.5rem',
          fontSize: '0.72rem',
          color: 'var(--color-text-muted)'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
            <span style={{ width: '9px', height: '9px', borderRadius: '50%', backgroundColor: '#ef4444' }} />
            High / Closure
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
            <span style={{ width: '9px', height: '9px', borderRadius: '50%', backgroundColor: '#f59e0b' }} />
            Medium Impact
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
            <span style={{ width: '9px', height: '9px', borderRadius: '50%', backgroundColor: '#0284c7' }} />
            Minor / Info
          </span>
        </div>
        <div style={{ fontStyle: 'italic' }}>
          💡 Click any highway marker to inspect lane details and schedule
        </div>
      </div>
    </div>
  );
};

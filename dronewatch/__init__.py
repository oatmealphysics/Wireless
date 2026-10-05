"""
dronewatch — unified passive drone detection & identification.

A rewrite/reorganisation of the elrs-sdr-transfer toolkit around one goal:
a single web dashboard that shows every drone around you, its protocol, and
its identifiers, by fusing multiple capture sources.

Layers:
  * protocols: crc, ota, fhss, lora          (ELRS exact ports — validated)
               opendroneid                    (Remote ID decoder — validated)
  * dsp:       dronescan, positivedet, wifi   (band scan + positive-ID detectors)
  * radio:     radio, uhd_bootstrap           (binding-tolerant UHD wrapper)
  * fusion:    fusion                          (sources -> identity-keyed tracks)
  * web:       webui                           (unified dashboard)
"""

__version__ = "0.2.0"

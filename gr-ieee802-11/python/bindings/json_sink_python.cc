/*
 * json_sink Python binding (pybind11).
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <pybind11/complex.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

#include <ieee802_11/json_sink.h>

void bind_json_sink(py::module& m)
{
    using json_sink = ::gr::ieee802_11::json_sink;

    py::class_<json_sink, gr::block, gr::basic_block,
               std::shared_ptr<json_sink>>(m, "json_sink")
        .def(py::init(&json_sink::make),
             py::arg("center_freq_hz"),
             py::arg("channel"),
             py::arg("include_raw") = false)
        ;
}

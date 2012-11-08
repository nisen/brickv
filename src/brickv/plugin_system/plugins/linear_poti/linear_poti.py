# -*- coding: utf-8 -*-  
"""
Linear Poti Plugin
Copyright (C) 2010 Olaf Lüke <olaf@tinkerforge.com>

poti.py: Poti Plugin implementation

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License 
as published by the Free Software Foundation; either version 2 
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public
License along with this program; if not, write to the
Free Software Foundation, Inc., 59 Temple Place - Suite 330,
Boston, MA 02111-1307, USA.
"""

from plugin_system.plugin_base import PluginBase
from plot_widget import PlotWidget
from bindings import ip_connection
from bindings.bricklet_linear_poti import BrickletLinearPoti

from PyQt4.QtGui import QVBoxLayout, QHBoxLayout, QLabel, QSlider
from PyQt4.QtCore import pyqtSignal, Qt

class PositionLabel(QLabel):
    def setText(self, text):
        text = "Position: " + text
        super(PositionLabel, self).setText(text)

class LinearPoti(PluginBase):
    qtcb_position = pyqtSignal(int)
    
    def __init__(self, ipcon, uid, version):
        PluginBase.__init__(self, ipcon, uid, 'Linear Poti Bricklet', version)
        
        self.lp = BrickletLinearPoti(uid, ipcon)
        
        self.qtcb_position.connect(self.cb_position)
        self.lp.register_callback(self.lp.CALLBACK_POSITION,
                                  self.qtcb_position.emit) 
        
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        
        self.position_label = PositionLabel('Position: ')
        
        self.current_value = 0
        plot_list = [['', Qt.red, self.get_current_value]]
        self.plot_widget = PlotWidget('Position', plot_list)
        
        layout_h = QHBoxLayout()
        layout_h.addStretch()
        layout_h.addWidget(self.position_label)
        layout_h.addWidget(self.slider)
        layout_h.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(layout_h)
        layout.addWidget(self.plot_widget)
        
    def start(self):
        try:
            self.cb_position(self.lp.get_position())
        except ip_connection.Error:
            pass
        
        try:
            self.lp.set_position_callback_period(20)
        except ip_connection.Error:
            return
        
        self.plot_widget.stop = False
        
    def stop(self):
        try:
            self.lp.set_position_callback_period(0)
        except ip_connection.Error:
            pass
        
        self.plot_widget.stop = True

    @staticmethod
    def has_device_identifier(device_identifier):
        return device_identifier == BrickletLinearPoti.DEVICE_IDENTIFIER

    def get_current_value(self):
        return self.current_value

    def cb_position(self, position):
        self.current_value = position
        self.slider.setValue(position)
        self.position_label.setText(str(position))

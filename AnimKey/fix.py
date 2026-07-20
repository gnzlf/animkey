import sys

file_path = r'c:\Users\ANIM-gonzalo\Google Drive Streaming\My Drive\Animacion\personal\ANIMKEY\ANIMKEY\AnimKey\core\toolbar.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the start of the missing block
start_idx = content.find('            append_widget(name)\n        ))\n        btn_right.clicked.connect(lambda: self._move_keyframes(1))\n        layout.addWidget(btn_right)\n        \n        return widget')

if start_idx == -1:
    start_idx = content.find('            append_widget(name)\r\n        ))\r\n        btn_right.clicked.connect(lambda: self._move_keyframes(1))\r\n        layout.addWidget(btn_right)\r\n        \r\n        return widget')

if start_idx != -1:
    end_idx = start_idx + len('            append_widget(name)\n        ))\n        btn_right.clicked.connect(lambda: self._move_keyframes(1))\n        layout.addWidget(btn_right)\n        \n        return widget')
    if content.find('            append_widget(name)\r\n') != -1:
        end_idx = start_idx + len('            append_widget(name)\r\n        ))\r\n        btn_right.clicked.connect(lambda: self._move_keyframes(1))\r\n        layout.addWidget(btn_right)\r\n        \r\n        return widget')
    
    replacement = '''            append_widget(name)
        
        for i in range(main_layout.count()):
            w = main_layout.itemAt(i).widget()
            if w and w not in widgets_to_insert and w.objectName():
                widgets_to_insert.append(w)
        
        while main_layout.count():
            main_layout.takeAt(0)
        
        for w in widgets_to_insert:
            main_layout.addWidget(w)
        main_layout.addStretch()

    def get_layout_order(self):
        order = []
        if not hasattr(self, 'content_widget') or not self.content_widget:
            return order
        main_layout = self.content_widget.layout()
        if not main_layout:
            return order
        for i in range(main_layout.count()):
            w = main_layout.itemAt(i).widget()
            if w and w.objectName():
                order.append(w.objectName())
        return order
    
    def _create_increase_values_block(self):
        """Create the increase/decrease values block (- [spinbox] +)"""
        scale = float(self.config.get("toolbar_scale", 1.0))
        theme = ThemeManager.get_current_theme()
        from AnimKey.mods import mediaMod as media
        
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        # Decrease button (-)
        btn_minus = ui.AnimKeyButton("", icon=media.get_icon("animkey_btn_Decrease_128.png"), button_type="small")
        btn_minus.setFixedSize(int(26 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        btn_minus.setToolTip(ui.create_tooltip_text(
            "Decrease Values",
            "Decrease keyframe or attribute values by the specified amount"
        ))
        btn_minus.clicked.connect(self._remove_inbetween)
        layout.addWidget(btn_minus)
        
        # Value amount spinbox
        self.frame_count_spinbox = QtWidgets.QDoubleSpinBox()
        self.frame_count_spinbox.setFixedSize(int(65 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        self.frame_count_spinbox.setMinimum(0.001)
        self.frame_count_spinbox.setMaximum(100.0)
        self.frame_count_spinbox.setValue(0.001)
        self.frame_count_spinbox.setSingleStep(0.001)
        self.frame_count_spinbox.setDecimals(3)
        self.frame_count_spinbox.setStyleSheet(f\'\'\'
            QDoubleSpinBox {{
                color: {theme["text_primary"]};
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }}
            QDoubleSpinBox:focus {{
                border-color: {theme["accent_primary"]};
            }}
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                background-color: {theme["button_bg"]};
                border: none;
                width: 16px;
            }}
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
                background-color: {theme["button_hover"]};
            }}
        \'\'\')
        self.frame_count_spinbox.setToolTip(ui.create_tooltip_text(
            "Value Amount",
            "Amount to increase or decrease keyframe/attribute values"
        ))
        layout.addWidget(self.frame_count_spinbox)
        
        # Increase button (+)
        btn_plus = ui.AnimKeyButton("", icon=media.get_icon("animkey_btn_Increase_128.png"), button_type="small")
        btn_plus.setFixedSize(int(26 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        btn_plus.setToolTip(ui.create_tooltip_text(
            "Increase Values",
            "Increase keyframe or attribute values by the specified amount"
        ))
        btn_plus.clicked.connect(self._add_inbetween)
        layout.addWidget(btn_plus)
        
        return widget
    
    def _create_move_keys_block(self):
        """Create the move keys block (< [spinbox] >)"""
        theme = ThemeManager.get_current_theme()
        from AnimKey.mods import mediaMod as media
        
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        # Move key left button (<)
        btn_left = ui.AnimKeyButton("", icon=media.get_icon("animkey_btn_LeftKeys_128.png"), button_type="small")
        btn_left.setFixedSize(int(26 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        btn_left.setToolTip(ui.create_tooltip_text(
            "Move Keys Left",
            "Move selected keyframes to the left by the specified amount",
            {"Shift+Click": "Move by 5x amount"}
        ))
        btn_left.clicked.connect(lambda: self._move_keyframes(-1))
        layout.addWidget(btn_left)
        
        # Key offset spinbox
        self.key_offset_spinbox = QtWidgets.QSpinBox()
        self.key_offset_spinbox.setFixedSize(int(45 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        self.key_offset_spinbox.setMinimum(1)
        self.key_offset_spinbox.setMaximum(100)
        self.key_offset_spinbox.setValue(1)
        self.key_offset_spinbox.setStyleSheet(f\'\'\'
            QSpinBox {{
                color: {theme["text_primary"]};
                background-color: {theme["bg_secondary"]};
                border: 1px solid {theme["border_color"]};
                border-radius: 3px;
                padding: 2px 4px;
                font-size: 11px;
            }}
            QSpinBox:focus {{
                border-color: {theme["accent_primary"]};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {theme["button_bg"]};
                border: none;
                width: 14px;
            }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
                background-color: {theme["button_hover"]};
            }}
        \'\'\')
        self.key_offset_spinbox.setToolTip(ui.create_tooltip_text(
            "Key Offset Frames",
            "Number of frames to move keyframes left or right"
        ))
        layout.addWidget(self.key_offset_spinbox)
        
        # Move key right button (>)
        btn_right = ui.AnimKeyButton("", icon=media.get_icon("animkey_btn_RightKeys_128.png"), button_type="small")
        btn_right.setFixedSize(int(26 * float(self.config.get("toolbar_scale", 1.0))), int(26 * float(self.config.get("toolbar_scale", 1.0))))
        btn_right.setToolTip(ui.create_tooltip_text(
            "Move Keys Right",
            "Move selected keyframes to the right by the specified amount",
            {"Shift+Click": "Move by 5x amount"}
        ))
        btn_right.clicked.connect(lambda: self._move_keyframes(1))
        layout.addWidget(btn_right)
        
        return widget'''
    
    new_content = content[:start_idx] + replacement + content[end_idx:]
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('SUCCESS')
else:
    print('FAILED TO FIND TARGET STRING')

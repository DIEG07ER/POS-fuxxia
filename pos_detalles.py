import sqlite3
import customtkinter as ctk
from customtkinter import filedialog
from datetime import datetime
import os
import shutil
import qrcode
from PIL import Image, ImageDraw, ImageFont
import csv
import barcode
from barcode.writer import ImageWriter
import io
import subprocess
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from collections import Counter, defaultdict
import numpy as np
import pyzipper
import smtplib
from email.mime.text import MIMEText
import random
import sys

# ==========================================
# CONFIGURACIÓN DE CORREO Y BÓVEDA
# ==========================================
VAULT_FILE = "Boveda_Fuxxia.dat"
VAULT_PWD = b"fuxxia_seguro_2026"

EMAIL_SENDER = "fuxxia.sistema@gmail.com" 
EMAIL_APP_PWD = "ancreililpydcfpt"

# ==========================================
# GESTIÓN DE BÓVEDA CIFRADA
# ==========================================
def save_to_vault(filename, data_bytes):
    mode = 'a' if os.path.exists(VAULT_FILE) else 'w'
    with pyzipper.AESZipFile(VAULT_FILE, mode, compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(VAULT_PWD)
        zf.writestr(filename, data_bytes)

def read_from_vault(filename):
    try:
        with pyzipper.AESZipFile(VAULT_FILE, 'r') as zf:
            zf.setpassword(VAULT_PWD)
            return zf.read(filename)
    except: return None

def extract_to_temp_and_open(filename):
    data = read_from_vault(filename)
    if not data: return False
    temp_dir = os.path.join(os.environ['TEMP'], 'fuxxia_temp')
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, os.path.basename(filename))
    with open(temp_path, 'wb') as f: f.write(data)
    try: os.startfile(temp_path); return True
    except: return False

# ==========================================
# BASE DE DATOS Y USUARIOS
# ==========================================
def conectar_db():
    conexion = sqlite3.connect('pos_tienda.db')
    cursor = conexion.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS inventario (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, variante TEXT, costo_codigo TEXT, fecha_codigo TEXT, precio REAL NOT NULL, stock INTEGER NOT NULL, ruta_imagen TEXT, ruta_qr TEXT, ruta_barras TEXT, fecha_ingreso TEXT, orden INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS ventas (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha_venta TEXT, cliente_nombre TEXT, cliente_cc TEXT, cliente_telefono TEXT, cliente_transaccion TEXT, valor_transaccion REAL DEFAULT 0, total_cobrado REAL, aplico_iva BOOLEAN, detalle_articulos TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gastos (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, cuestion TEXT, valor REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS historial_eliminaciones (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha_eliminacion TEXT, tipo TEXT, detalle TEXT, info_extra TEXT, ruta_img TEXT)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, email TEXT,
        rol TEXT DEFAULT 'cajero', p_crear_inv BOOLEAN DEFAULT 0, p_elim_inv BOOLEAN DEFAULT 0, 
        p_elim_fac BOOLEAN DEFAULT 0, p_ver_macros BOOLEAN DEFAULT 0)''')

    if cursor.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
        cursor.execute("INSERT INTO usuarios (username, password, email, rol, p_crear_inv, p_elim_inv, p_elim_fac, p_ver_macros) VALUES (?, ?, ?, ?, 1, 1, 1, 1)", ("admin", "1234", EMAIL_SENDER, "admin"))

    cols_inv = [r[1] for r in cursor.execute("PRAGMA table_info(inventario)").fetchall()]
    for col, dval in [('ruta_imagen','TEXT'),('ruta_qr','TEXT'),('ruta_barras','TEXT'),('costo_codigo','TEXT'),('fecha_codigo','TEXT'),('fecha_ingreso','TEXT'),('orden','INTEGER DEFAULT 0')]:
        if col not in cols_inv: cursor.execute(f"ALTER TABLE inventario ADD COLUMN {col} {dval}")
    cols_ven = [r[1] for r in cursor.execute("PRAGMA table_info(ventas)").fetchall()]
    for col, dt in [('cliente_telefono','TEXT'), ('cliente_transaccion','TEXT'), ('valor_transaccion','REAL DEFAULT 0')]:
        if col not in cols_ven: cursor.execute(f"ALTER TABLE ventas ADD COLUMN {col} {dt}")
    cols_hist = [r[1] for r in cursor.execute("PRAGMA table_info(historial_eliminaciones)").fetchall()]
    if 'info_extra' not in cols_hist: cursor.execute("ALTER TABLE historial_eliminaciones ADD COLUMN info_extra TEXT")
    if 'ruta_img' not in cols_hist: cursor.execute("ALTER TABLE historial_eliminaciones ADD COLUMN ruta_img TEXT")
    
    cursor.execute("UPDATE inventario SET orden = id WHERE orden = 0 OR orden IS NULL")
    conexion.commit()
    return conexion

def registrar_eliminacion(tipo, detalle, info_extra="", ruta_img=""):
    con = conectar_db(); fecha = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    con.cursor().execute("INSERT INTO historial_eliminaciones (fecha_eliminacion, tipo, detalle, info_extra, ruta_img) VALUES (?,?,?,?,?)", (fecha, tipo, detalle, info_extra, ruta_img))
    con.commit(); con.close()

# ==========================================
# UTILIDADES Y LÓGICA DE COSTOS
# ==========================================
def fmt_precio(valor):
    try: return f"${int(round(float(valor))):,}".replace(",", ".")
    except: return f"${valor}"
def fmt_numero(valor):
    try: return f"{int(round(float(valor))):,}".replace(",", ".")
    except: return str(valor)

def formatear_costo_logica(costo_str):
    try: limpio = str(costo_str).replace('.','').replace(',','').strip(); texto = str(int(float(limpio)))
    except: return str(costo_str)
    if len(texto) <= 3: return texto.rstrip('0') if texto.rstrip('0') else "0"
    else: miles, cientos = texto[:-3], texto[-3:].rstrip('0'); return f"{miles}.{cientos}" if cientos else f"{miles}.0"
def codificar_pandetrigo(texto):
    clave = {'1':'P','2':'A','3':'N','4':'D','5':'E','6':'T','7':'R','8':'I','9':'G','0':'O'}
    return "".join(clave.get(char, char) for char in str(texto))
def decodificar_costo(codigo):
    inversa = {'P':'1','A':'2','N':'3','D':'4','E':'5','T':'6','R':'7','I':'8','G':'9','O':'0'}
    return "".join(inversa.get(c, c) for c in str(codigo))
def costo_real(costo_cod):
    try:
        dec = decodificar_costo(costo_cod)
        if '.' in dec: return float(dec) * 1000 
        else: return float(dec.ljust(3, '0'))   
    except: return 0.0
def obtener_fecha_codificada():
    return codificar_pandetrigo(datetime.now().strftime("%d.%m.%y"))

def generar_codigo_barras(id_numero, info_texto, nombre_archivo):
    options = {"write_text": False, "module_width": 0.45, "module_height": 11.0, "quiet_zone": 6.5}
    Code128 = barcode.get_barcode_class('code128')
    temp_bar = "temp_bar"
    Code128(str(id_numero), writer=ImageWriter()).save(temp_bar, options=options)
    temp_bar += ".png"
    try:
        img = Image.open(temp_bar)
        nueva = Image.new("RGB", (max(img.width, 280), img.height + 40), "white")
        nueva.paste(img, ((nueva.width - img.width) // 2, 0))
        draw = ImageDraw.Draw(nueva)
        try: font = ImageFont.truetype("arial.ttf", 24)
        except: font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), info_texto, font=font)
        draw.text(((nueva.width - (bbox[2] - bbox[0])) // 2, img.height + 5), info_texto, fill="black", font=font)
        buf = io.BytesIO()
        nueva.save(buf, format='PNG')
        save_to_vault(nombre_archivo, buf.getvalue())
        nueva.close(); img.close()
    except Exception as e: print("Error dibujando barras:", e)
    finally:
        if os.path.exists(temp_bar): os.remove(temp_bar)
    return nombre_archivo 

def copiar_img_portapapeles(ruta):
    try:
        ruta_abs = os.path.abspath(ruta)
        cmd = f'powershell -command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile(\'{ruta_abs}\'))"'
        subprocess.run(cmd, shell=True, creationflags=0x08000000)
    except Exception as e: print("Error copiando:", e)

# ==========================================
# DIÁLOGO DE CONTRASEÑA 
# ==========================================
def pedir_password(parent, titulo="Verificación"):
    resultado = [False]
    win = ctk.CTkToplevel(parent); win.title(titulo); win.geometry("300x180"); win.attributes("-topmost", True); win.grab_set()
    ctk.CTkLabel(win, text="🔒 Contraseña Admin requerida", font=("Arial", 14, "bold")).pack(pady=12)
    entry = ctk.CTkEntry(win, show="*", width=220, placeholder_text="Contraseña admin"); entry.pack(pady=8)
    lbl_err = ctk.CTkLabel(win, text="", text_color="red"); lbl_err.pack()
    
    def verificar(*args):
        con = conectar_db()
        admin_pw = con.cursor().execute("SELECT password FROM usuarios WHERE rol='admin' LIMIT 1").fetchone()
        con.close()
        clave_real = admin_pw[0] if admin_pw else "1234"

        if entry.get() == clave_real: 
            resultado[0] = True; win.destroy()
        else: 
            lbl_err.configure(text="❌ Contraseña incorrecta"); entry.delete(0, 'end')
            
    entry.bind("<Return>", verificar)
    ctk.CTkButton(win, text="Confirmar", command=verificar, width=180).pack(pady=8)
    parent.wait_window(win)
    return resultado[0]

# ==========================================
# INTERFAZ PRINCIPAL
# ==========================================
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

class AppPOS(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.withdraw() 
        self.current_user = None
        self.protocol("WM_DELETE_WINDOW", self.cerrar_programa)
        self.mostrar_login()

    def cerrar_programa(self):
        self.quit()
        self.destroy()
        sys.exit(0)

    # --- LOGIN ---
    def mostrar_login(self):
        self.login_win = ctk.CTkToplevel(self)
        self.login_win.title("FUXXIA — Acceso")
        self.login_win.geometry("350x350")
        self.login_win.attributes("-topmost", True)
        self.login_win.protocol("WM_DELETE_WINDOW", self.cerrar_programa)
        
        ctk.CTkLabel(self.login_win, text="🔑 FUXXIA", font=("Arial", 26, "bold")).pack(pady=15)
        ctk.CTkLabel(self.login_win, text="Tienda de Detalles", font=("Arial", 13)).pack()
        
        self.entry_usr = ctk.CTkEntry(self.login_win, placeholder_text="Usuario")
        self.entry_usr.pack(pady=10)
        self.entry_pwd = ctk.CTkEntry(self.login_win, placeholder_text="Contraseña", show="*")
        self.entry_pwd.pack(pady=6)
        self.lbl_err = ctk.CTkLabel(self.login_win, text="", text_color="red")
        self.lbl_err.pack()

        def verificar(*args):
            usr, pwd = self.entry_usr.get().strip(), self.entry_pwd.get().strip()
            con = conectar_db()
            user_data = con.cursor().execute("SELECT id, username, rol, p_crear_inv, p_elim_inv, p_elim_fac, p_ver_macros FROM usuarios WHERE username=? AND password=?", (usr, pwd)).fetchone()
            con.close()
            if user_data:
                self.current_user = {"id": user_data[0], "username": user_data[1], "rol": user_data[2], "p_crear_inv": bool(user_data[3]), "p_elim_inv": bool(user_data[4]), "p_elim_fac": bool(user_data[5]), "p_ver_macros": bool(user_data[6])}
                self.login_win.destroy(); self.iniciar_aplicacion()
            else: self.lbl_err.configure(text="Usuario o contraseña incorrectos")

        self.entry_pwd.bind("<Return>", verificar)
        ctk.CTkButton(self.login_win, text="Ingresar", command=verificar).pack(pady=10)
        ctk.CTkButton(self.login_win, text="Olvidé mi contraseña", fg_color="transparent", text_color="blue", command=self.recuperar_password).pack(pady=5)

    def recuperar_password(self):
        usr = self.entry_usr.get().strip()
        if not usr: self.lbl_err.configure(text="Escribe tu usuario arriba para recuperar"); return
        con = conectar_db()
        user_exists = con.cursor().execute("SELECT id FROM usuarios WHERE username=?", (usr,)).fetchone()
        con.close()
        if not user_exists: self.lbl_err.configure(text="Este usuario no existe"); return
        
        codigo = str(random.randint(1000, 9999))
        self.lbl_err.configure(text="Enviando correo al administrador...", text_color="blue"); self.login_win.update()
        
        try:
            msg = MIMEText(f"El sistema FUXXIA solicita restablecer contraseña.\nUsuario afectado: {usr}\n\nCódigo de seguridad: {codigo}")
            msg['Subject'] = "Seguridad FUXXIA - Recuperación"
            msg['From'] = EMAIL_SENDER; msg['To'] = EMAIL_SENDER 
            server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
            server.login(EMAIL_SENDER, EMAIL_APP_PWD)
            server.send_message(msg); server.quit()
        except Exception as e:
            self.lbl_err.configure(text="Error de conexión (Revisa credenciales Gmail)", text_color="red"); return

        self.lbl_err.configure(text="Código enviado al correo maestro.", text_color="green")
        req_win = ctk.CTkToplevel(self.login_win); req_win.geometry("300x250"); req_win.title("Recuperar"); req_win.attributes("-topmost", True)
        ctk.CTkLabel(req_win, text=f"Código enviado al correo del Admin.", font=("Arial", 12, "bold")).pack(pady=10)
        ent_cod = ctk.CTkEntry(req_win, placeholder_text="Código de 4 dígitos"); ent_cod.pack(pady=5)
        ent_new = ctk.CTkEntry(req_win, placeholder_text="Nueva contraseña", show="*"); ent_new.pack(pady=5)
        lbl_r = ctk.CTkLabel(req_win, text="", text_color="red"); lbl_r.pack()
        
        def cambiar():
            if ent_cod.get().strip() == codigo:
                if ent_new.get().strip():
                    c = conectar_db(); c.cursor().execute("UPDATE usuarios SET password=? WHERE username=?", (ent_new.get().strip(), usr)); c.commit(); c.close()
                    req_win.destroy(); self.lbl_err.configure(text="Contraseña actualizada. Inicia sesión.", text_color="green")
                else: lbl_r.configure(text="La contraseña no puede estar vacía")
            else: lbl_r.configure(text="Código incorrecto")
        ctk.CTkButton(req_win, text="Cambiar Contraseña", command=cambiar).pack(pady=10)

    # --- INICIO DEL SISTEMA ---
    def iniciar_aplicacion(self):
        self.deiconify(); self.title(f"Sistema POS - FUXXIA | Usuario: {self.current_user['username'].upper()}")
        self.geometry("1250x800")
        self.conexion = conectar_db()
        self.ruta_imagen_seleccionada = None; self.carrito_compras = []; self.porcentaje_iva = 0.19

        for carpeta in ['exportaciones']:
            if not os.path.exists(carpeta): os.makedirs(carpeta)

        self.tabview = ctk.CTkTabview(self, width=1200, height=750); self.tabview.pack(padx=10, pady=10, fill="both", expand=True)
        
        self.tab_facturacion = self.tabview.add("💰 Caja y Facturación")
        self.tab_inventario  = self.tabview.add("📦 Ver Inventario")
        
        if self.current_user['p_crear_inv'] or self.current_user['rol'] == 'admin':
            self.tab_ingreso = self.tabview.add("➕ Ingreso de Artículos")
            self.construir_pestaña_ingreso()
            
        self.tab_reportes = self.tabview.add("📊 Reportes y Ventas")
        
        if self.current_user['rol'] == 'admin':
            self.tab_admin = self.tabview.add("⚙️ Panel Admin")
            self.construir_pestaña_admin()

        self.construir_pestaña_facturacion()
        self.construir_pestaña_inventario()
        self.construir_pestaña_reportes()
        
        self.tabview.configure(command=self._on_tab_change)

    def _on_tab_change(self):
        tab = self.tabview.get()
        if tab == "📦 Ver Inventario": self.buscar_articulos()
        elif tab == "📊 Reportes y Ventas": self.cargar_ventas(); self.cargar_gastos()
        elif tab == "⚙️ Panel Admin":
            if not pedir_password(self, "Acceso exclusivo Admin"):
                self.tabview.set("💰 Caja y Facturación") 
                return
            self.cargar_lista_usrs()

    # ==========================================
    # PESTAÑA: PANEL ADMIN (RBAC + EDICIÓN)
    # ==========================================
    def construir_pestaña_admin(self):
        frame_izq = ctk.CTkFrame(self.tab_admin, width=350); frame_izq.pack(side="left", fill="y", padx=10, pady=10)
        self.frame_der_admin = ctk.CTkScrollableFrame(self.tab_admin); self.frame_der_admin.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(frame_izq, text="👤 Crear Nuevo Usuario", font=("Arial", 18, "bold")).pack(pady=10)
        e_usr = ctk.CTkEntry(frame_izq, placeholder_text="Username", width=250); e_usr.pack(pady=5)
        e_pwd = ctk.CTkEntry(frame_izq, placeholder_text="Contraseña", width=250); e_pwd.pack(pady=5)
        
        ctk.CTkLabel(frame_izq, text="Permisos (Saltar Contraseña):", font=("Arial", 14, "bold")).pack(pady=(15,0))
        chk_crear = ctk.CTkCheckBox(frame_izq, text="Crear Inventario"); chk_crear.pack(anchor="w", padx=40, pady=4)
        chk_elimi = ctk.CTkCheckBox(frame_izq, text="Eliminar Inventario"); chk_elimi.pack(anchor="w", padx=40, pady=4)
        chk_elimf = ctk.CTkCheckBox(frame_izq, text="Eliminar Facturas"); chk_elimf.pack(anchor="w", padx=40, pady=4)
        chk_macro = ctk.CTkCheckBox(frame_izq, text="Ver Macros y Utilidad"); chk_macro.pack(anchor="w", padx=40, pady=4)
        lbl_msg = ctk.CTkLabel(frame_izq, text=""); lbl_msg.pack()

        def crear_usr():
            u, p = e_usr.get().strip(), e_pwd.get().strip()
            if not u or not p: lbl_msg.configure(text="Usuario y clave requeridos", text_color="red"); return
            try:
                c = self.conexion.cursor()
                c.execute("INSERT INTO usuarios (username, password, email, p_crear_inv, p_elim_inv, p_elim_fac, p_ver_macros) VALUES (?,?,?,?,?,?,?)", 
                          (u, p, EMAIL_SENDER, chk_crear.get(), chk_elimi.get(), chk_elimf.get(), chk_macro.get()))
                self.conexion.commit(); lbl_msg.configure(text="✅ Usuario Creado", text_color="green")
                e_usr.delete(0,'end'); e_pwd.delete(0,'end'); self.cargar_lista_usrs()
            except: lbl_msg.configure(text="❌ El usuario ya existe", text_color="red")

        ctk.CTkButton(frame_izq, text="Registrar Cajero", command=crear_usr).pack(pady=10)
        self.cargar_lista_usrs()

    def cargar_lista_usrs(self):
        for w in self.frame_der_admin.winfo_children(): w.destroy()
        ctk.CTkLabel(self.frame_der_admin, text="Lista de Usuarios Activos", font=("Arial", 16, "bold")).pack(pady=5)
        usrs = self.conexion.cursor().execute("SELECT id, username, rol FROM usuarios").fetchall()
        for u_id, u_name, rol in usrs:
            f = ctk.CTkFrame(self.frame_der_admin, fg_color="white"); f.pack(fill="x", pady=2)
            ctk.CTkLabel(f, text=f"👤 {u_name} ({rol.upper()})", font=("Arial", 14), text_color="black").pack(side="left", padx=10, pady=5)
            
            btn_frame = ctk.CTkFrame(f, fg_color="transparent"); btn_frame.pack(side="right", padx=10)
            ctk.CTkButton(btn_frame, text="✏️ Editar", fg_color="orange", width=60, command=lambda uid=u_id: self.editar_usuario(uid)).pack(side="left", padx=5)
            if rol != 'admin': ctk.CTkButton(btn_frame, text="🗑️", fg_color="red", width=30, command=lambda uid=u_id: self.elim_usr(uid)).pack(side="left", padx=5)

    def elim_usr(self, uid):
        self.conexion.cursor().execute("DELETE FROM usuarios WHERE id=?", (uid,)); self.conexion.commit(); self.cargar_lista_usrs()

    def editar_usuario(self, uid):
        u_data = self.conexion.cursor().execute("SELECT username, password, p_crear_inv, p_elim_inv, p_elim_fac, p_ver_macros, rol FROM usuarios WHERE id=?", (uid,)).fetchone()
        if not u_data: return
        uname, upwd, c_inv, e_inv, e_fac, v_mac, rol = u_data

        win = ctk.CTkToplevel(self); win.title(f"Editar Usuario: {uname}"); win.geometry("350x450"); win.attributes("-topmost", True)
        ctk.CTkLabel(win, text=f"Editando a: {uname}", font=("Arial", 16, "bold")).pack(pady=15)
        
        ctk.CTkLabel(win, text="Contraseña:").pack(anchor="w", padx=40)
        e_pwd = ctk.CTkEntry(win, width=250); e_pwd.insert(0, upwd); e_pwd.pack(pady=5)
        
        chk_c_inv = ctk.CTkCheckBox(win, text="Crear Inventario"); chk_c_inv.pack(anchor="w", padx=40, pady=5)
        chk_e_inv = ctk.CTkCheckBox(win, text="Eliminar Inventario"); chk_e_inv.pack(anchor="w", padx=40, pady=5)
        chk_e_fac = ctk.CTkCheckBox(win, text="Eliminar Facturas"); chk_e_fac.pack(anchor="w", padx=40, pady=5)
        chk_v_mac = ctk.CTkCheckBox(win, text="Ver Macros y Utilidad"); chk_v_mac.pack(anchor="w", padx=40, pady=5)
        
        if c_inv: chk_c_inv.select()
        if e_inv: chk_e_inv.select()
        if e_fac: chk_e_fac.select()
        if v_mac: chk_v_mac.select()

        if rol == 'admin':
            chk_c_inv.configure(state="disabled"); chk_e_inv.configure(state="disabled")
            chk_e_fac.configure(state="disabled"); chk_v_mac.configure(state="disabled")
            ctk.CTkLabel(win, text="(El Admin tiene todos los permisos)", text_color="gray").pack()

        def guardar():
            self.conexion.cursor().execute("UPDATE usuarios SET password=?, p_crear_inv=?, p_elim_inv=?, p_elim_fac=?, p_ver_macros=? WHERE id=?", 
                                           (e_pwd.get().strip(), chk_c_inv.get(), chk_e_inv.get(), chk_e_fac.get(), chk_v_mac.get(), uid))
            self.conexion.commit(); win.destroy()
            if self.current_user['id'] == uid: self.current_user['p_crear_inv'] = chk_c_inv.get() 

        ctk.CTkButton(win, text="Guardar Cambios", fg_color="blue", command=guardar).pack(pady=20)


    # ==========================================
    # CAJA Y FACTURACIÓN
    # ==========================================
    def construir_pestaña_facturacion(self):
        frame_izq = ctk.CTkFrame(self.tab_facturacion, width=480); frame_izq.pack(side="left", fill="y", padx=10, pady=10)
        frame_der = ctk.CTkFrame(self.tab_facturacion); frame_der.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(frame_izq, text="Buscar Producto para Vender", font=("Arial", 18, "bold")).pack(pady=10)
        self.entry_buscar_venta = ctk.CTkEntry(frame_izq, placeholder_text="Escanea QR, Barras o escribe...", width=340); self.entry_buscar_venta.pack(pady=5)
        self.entry_buscar_venta.bind("<Button-1>", lambda e: self.entry_buscar_venta.delete(0, 'end'))
        self.entry_buscar_venta.bind("<FocusIn>", lambda e: self.entry_buscar_venta.delete(0, 'end'))
        self.entry_buscar_venta.bind("<Return>", lambda e: self.buscar_para_vender())
        ctk.CTkButton(frame_izq, text="Buscar", command=self.buscar_para_vender).pack(pady=5)
        self.frame_resultados_venta = ctk.CTkScrollableFrame(frame_izq, width=440, height=320); self.frame_resultados_venta.pack(pady=10, fill="both", expand=True)

        ctk.CTkLabel(frame_der, text="Carrito de Compras", font=("Arial", 18, "bold")).pack(pady=5)
        self.frame_carrito_lista = ctk.CTkScrollableFrame(frame_der); self.frame_carrito_lista.pack(fill="both", expand=True, padx=10, pady=5)

        frame_controles = ctk.CTkFrame(frame_der, fg_color="transparent"); frame_controles.pack(fill="x", padx=10, pady=5, side="bottom")
        self.switch_iva = ctk.CTkSwitch(frame_controles, text="Aplicar IVA (19%)", command=self.actualizar_totales_carrito); self.switch_iva.pack(pady=5)
        self.label_total = ctk.CTkLabel(frame_controles, text="TOTAL: $0", font=("Arial", 24, "bold"), text_color="green"); self.label_total.pack(pady=5)

        frame_datos = ctk.CTkFrame(frame_controles, fg_color="transparent"); frame_datos.pack(pady=5)
        W = 150
        self.entry_cli_nombre = ctk.CTkEntry(frame_datos, placeholder_text="Cliente / Razón", width=W); self.entry_cli_nombre.grid(row=0, column=0, padx=5, pady=5)
        self.entry_cli_cc = ctk.CTkEntry(frame_datos, placeholder_text="CC / NIT", width=W); self.entry_cli_cc.grid(row=0, column=1, padx=5, pady=5)
        self.entry_cli_tel = ctk.CTkEntry(frame_datos, placeholder_text="Teléfono", width=W); self.entry_cli_tel.grid(row=0, column=2, padx=5, pady=5)
        
        self.var_usa_trans = ctk.BooleanVar(value=False)
        self.switch_trans = ctk.CTkSwitch(frame_datos, text="Pago Electrónico", variable=self.var_usa_trans, command=self.toggle_trans)
        self.switch_trans.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.frame_trans_opt = ctk.CTkFrame(frame_datos, fg_color="transparent", height=35)
        self.frame_trans_opt.grid(row=1, column=1, columnspan=2, sticky="w"); self.frame_trans_opt.grid_propagate(False) 
        self.cmb_trans = ctk.CTkComboBox(self.frame_trans_opt, values=["NEQUI", "DAVIPLATA", "OTROS"], width=120)
        self.entry_val_trans = ctk.CTkEntry(self.frame_trans_opt, placeholder_text="Valor Elec. ($)", width=120)

        self.btn_cobrar = ctk.CTkButton(frame_controles, text="💸 COMPLETAR E IMPRIMIR FACTURA", font=("Arial", 15, "bold"), fg_color="blue", height=50, command=self.facturar_venta)
        self.btn_cobrar.pack(pady=10, fill="x")

    def toggle_trans(self):
        if self.var_usa_trans.get():
            self.cmb_trans.pack(side="left", padx=5); self.entry_val_trans.pack(side="left", padx=5)
            sub = sum(item['precio'] for item in self.carrito_compras)
            tot = sub * 1.19 if self.switch_iva.get() else sub
            self.entry_val_trans.delete(0, 'end'); self.entry_val_trans.insert(0, str(int(tot)))
        else:
            self.cmb_trans.pack_forget(); self.entry_val_trans.pack_forget()

    def buscar_para_vender(self):
        for w in self.frame_resultados_venta.winfo_children(): w.destroy()
        termino = self.entry_buscar_venta.get().strip()
        if termino.lower().startswith("articulo:"): termino = termino[9:].split('\n')[0].strip()
        termino_like = f"%{termino}%"
        cursor = self.conexion.cursor()
        cursor.execute("SELECT id, nombre, variante, precio, stock, ruta_imagen FROM inventario WHERE CAST(id AS TEXT)=? OR nombre LIKE ? OR variante LIKE ? LIMIT 60", (termino, termino_like, termino_like))
        resultados = cursor.fetchall()

        if len(resultados) == 1 and termino.isdigit():
            self.agregar_al_carrito(*resultados[0][:3], resultados[0][3], resultados[0][5])
            self.entry_buscar_venta.delete(0, 'end'); self.buscar_para_vender(); return

        for id_item, nombre, var, precio, stock, img_ruta in resultados:
            if stock > 0:
                t = ctk.CTkFrame(self.frame_resultados_venta); t.pack(fill="x", pady=2, padx=2)
                lbl_mini = ctk.CTkLabel(t, text="", width=36, height=36)
                if img_ruta:
                    try:
                        b_data = read_from_vault(img_ruta)
                        if b_data:
                            img_pil = Image.open(io.BytesIO(b_data)); img_pil.load()
                            img_ctk = ctk.CTkImage(light_image=img_pil.copy(), size=(34, 34))
                            lbl_mini.configure(image=img_ctk); lbl_mini.image = img_ctk
                    except: lbl_mini.configure(text="📷")
                else: lbl_mini.configure(text="📷")
                lbl_mini.pack(side="left", padx=4)
                ctk.CTkLabel(t, text=f"{nombre} ({var}) - {fmt_precio(precio)} | Stk: {stock}").pack(side="left", padx=5)
                ctk.CTkButton(t, text="Añadir", width=60, fg_color="green", command=lambda i=id_item, n=nombre, v=var, p=precio, r=img_ruta: self.agregar_al_carrito(i, n, v, p, r)).pack(side="right", padx=5)
        self.entry_buscar_venta.delete(0, 'end')

    def agregar_al_carrito(self, id_item, nombre, variante, precio, img_ruta=None):
        self.carrito_compras.append({"id": id_item, "nombre": nombre, "variante": variante, "precio": precio, "img_ruta": img_ruta})
        self.actualizar_totales_carrito()

    def quitar_del_carrito(self, indice):
        self.carrito_compras.pop(indice); self.actualizar_totales_carrito()

    def actualizar_totales_carrito(self):
        for w in self.frame_carrito_lista.winfo_children(): w.destroy()
        subtotal = 0
        for i, item in enumerate(self.carrito_compras):
            row = ctk.CTkFrame(self.frame_carrito_lista, fg_color="white", corner_radius=8); row.pack(fill="x", pady=3, padx=4)
            lbl_img = ctk.CTkLabel(row, text="", width=46, height=46, fg_color="#eeeeee", corner_radius=6)
            if item.get("img_ruta"):
                try:
                    b_data = read_from_vault(item["img_ruta"])
                    if b_data:
                        img_pil = Image.open(io.BytesIO(b_data)); img_pil.load()
                        img_ctk = ctk.CTkImage(light_image=img_pil.copy(), size=(42, 42))
                        lbl_img.configure(image=img_ctk, fg_color="transparent"); lbl_img.image = img_ctk
                except: lbl_img.configure(text="📷", font=("Arial", 14))
            else: lbl_img.configure(text="📷")
            lbl_img.pack(side="left", padx=6, pady=4)
            ctk.CTkLabel(row, text=f"{item['nombre']} ({item.get('variante','')})  —  {fmt_precio(item['precio'])}", font=("Arial", 13)).pack(side="left", padx=8)
            ctk.CTkButton(row, text="✕", width=34, height=28, fg_color="red", command=lambda idx=i: self.quitar_del_carrito(idx)).pack(side="right", padx=8, pady=4)
            subtotal += item['precio']
        total = subtotal + (subtotal * self.porcentaje_iva) if self.switch_iva.get() else subtotal
        self.label_total.configure(text=f"TOTAL: {fmt_precio(total)}")
        if self.var_usa_trans.get(): self.entry_val_trans.delete(0, 'end'); self.entry_val_trans.insert(0, str(int(total)))

    def facturar_venta(self):
        if not self.carrito_compras: return
        fecha_hoy = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cliente, cc, tel = self.entry_cli_nombre.get() or "Consumidor Final", self.entry_cli_cc.get() or "N/A", self.entry_cli_tel.get() or "N/A"
        con_iva = self.switch_iva.get(); subtotal = sum(item['precio'] for item in self.carrito_compras)
        total = subtotal + (subtotal * self.porcentaje_iva) if con_iva else subtotal

        trans, valor_trans = "NO", 0.0
        if self.var_usa_trans.get():
            trans = self.cmb_trans.get()
            try: valor_trans = float(self.entry_val_trans.get().replace('.','').replace(',','.'))
            except: pass

        cursor = self.conexion.cursor()
        det_lista = []
        for item in self.carrito_compras:
            row = cursor.execute("SELECT costo_codigo FROM inventario WHERE id=?", (item['id'],)).fetchone()
            c_val = costo_real(row[0]) if row else 0
            det_lista.append(f"{item['nombre']}|{item['precio']}|{c_val}")
            cursor.execute("UPDATE inventario SET stock = stock - 1 WHERE id=?", (item['id'],))
        
        nombres_db = ", ".join(det_lista)
        cursor.execute('''INSERT INTO ventas (fecha_venta, cliente_nombre, cliente_cc, cliente_telefono, cliente_transaccion, valor_transaccion, total_cobrado, aplico_iva, detalle_articulos) VALUES (?,?,?,?,?,?,?,?,?)''', (fecha_hoy, cliente, cc, tel, trans, valor_trans, total, con_iva, nombres_db))
        id_venta = cursor.lastrowid; self.conexion.commit()

        txt_factura = f"{'='*44}\n{'F U X X I A':^44}\n{'TIENDA DE DETALLES':^44}\n{'='*44}\nFactura Nro: {id_venta}\nFecha: {fecha_hoy}\nCliente: {cliente}\nCC/NIT: {cc}\nTel: {tel}\n{'-'*44}\n"
        for item in self.carrito_compras: txt_factura += f"{item['nombre'][:26]:<26} $ {fmt_numero(item['precio']):>10}\n"
        txt_factura += f"{'-'*44}\n{'Subtotal:':<26} $ {fmt_numero(subtotal):>10}\n"
        if con_iva: txt_factura += f"{'IVA (19%):':<26} $ {fmt_numero(subtotal * self.porcentaje_iva):>10}\n"
        txt_factura += f"{'TOTAL A PAGAR:':<26} $ {fmt_numero(total):>10}\n{'-'*44}\n"
        if trans != "NO" and valor_trans > 0:
            txt_factura += f"Pagado vía {trans}: $ {fmt_numero(valor_trans)}\n"
            if valor_trans < total: txt_factura += f"Pagado en Efectivo: $ {fmt_numero(total - valor_trans)}\n"
        else: txt_factura += f"Pagado en Efectivo: $ {fmt_numero(total)}\n"
        txt_factura += f"{'='*44}\n{'¡Gracias por su compra!':^44}\n{'FUXXIA':^44}\n"
        
        ruta_factura_zip = f"facturas/Factura_{id_venta}.txt"
        save_to_vault(ruta_factura_zip, txt_factura.encode('utf-8'))
        extract_to_temp_and_open(ruta_factura_zip)
            
        self.carrito_compras.clear(); self.actualizar_totales_carrito()
        for e in [self.entry_cli_nombre, self.entry_cli_cc, self.entry_cli_tel]: e.delete(0,'end')
        self.var_usa_trans.set(False); self.toggle_trans(); self.buscar_articulos(); self.cargar_ventas()

    # ==========================================
    # INVENTARIO Y EDICIÓN
    # ==========================================
    def construir_pestaña_inventario(self):
        frame_buscador = ctk.CTkFrame(self.tab_inventario); frame_buscador.pack(fill="x", pady=10, padx=10)
        self.entry_buscar = ctk.CTkEntry(frame_buscador, placeholder_text="🔍 Escanea QR, Barras o escribe...", font=("Arial", 16))
        self.entry_buscar.pack(side="left", fill="x", expand=True, padx=10)
        self.entry_buscar.bind("<Button-1>", lambda e: self.entry_buscar.delete(0, 'end'))
        self.entry_buscar.bind("<FocusIn>", lambda e: self.entry_buscar.delete(0, 'end'))
        self.entry_buscar.bind("<Return>", lambda e: self.buscar_articulos())
        ctk.CTkButton(frame_buscador, text="Buscar", command=self.buscar_articulos).pack(side="right", padx=10)
        self.frame_resultados = ctk.CTkScrollableFrame(self.tab_inventario); self.frame_resultados.pack(pady=10, fill="both", expand=True); self.buscar_articulos()

    def buscar_articulos(self):
        for w in self.frame_resultados.winfo_children(): w.destroy()
        termino = self.entry_buscar.get().strip()
        if termino.lower().startswith("articulo:"): termino = termino[9:].split('\n')[0].strip()
        termino_like = f"%{termino}%"
        cursor = self.conexion.cursor()
        cursor.execute("""SELECT id, nombre, variante, costo_codigo, precio, stock, ruta_imagen, fecha_ingreso, ruta_qr, ruta_barras FROM inventario WHERE CAST(id AS TEXT)=? OR nombre LIKE ? OR variante LIKE ? ORDER BY orden ASC, id DESC LIMIT 60""", (termino, termino_like, termino_like))
        filas = cursor.fetchall(); ids_orden = [f[0] for f in filas]

        for idx, item in enumerate(filas):
            id_item, nombre, var, costo, precio, stock, img_ruta, fecha_ing, r_qr, r_barras = item
            tarjeta = ctk.CTkFrame(self.frame_resultados, fg_color="white", corner_radius=10); tarjeta.pack(fill="x", pady=5, padx=10)
            col_mover = ctk.CTkFrame(tarjeta, fg_color="transparent", width=35); col_mover.pack(side="left", padx=4, pady=5)
            ctk.CTkButton(col_mover, text="▲", width=30, height=28, fg_color="#aaaaaa", command=lambda i=id_item, lst=ids_orden, pos=idx: self.mover_articulo(i, lst, pos, -1)).pack(pady=2)
            ctk.CTkButton(col_mover, text="▼", width=30, height=28, fg_color="#aaaaaa", command=lambda i=id_item, lst=ids_orden, pos=idx: self.mover_articulo(i, lst, pos, +1)).pack(pady=2)

            lbl_img = ctk.CTkLabel(tarjeta, text="📷\nSin Foto", width=70, height=70, fg_color="lightgray", corner_radius=6)
            if img_ruta:
                try:
                    b_data = read_from_vault(img_ruta)
                    if b_data:
                        img_pil = Image.open(io.BytesIO(b_data)); img_pil.load()
                        img_ctk = ctk.CTkImage(light_image=img_pil.copy(), size=(65, 65))
                        lbl_img.configure(image=img_ctk, text="", fg_color="transparent"); lbl_img.image = img_ctk
                except: pass
            lbl_img.pack(side="left", padx=8, pady=8)

            info = (f"ID: {id_item} | {nombre} ({var})\nCódigo: {costo}  |  Precio: {fmt_precio(precio)}  |  Stock: {stock}\n📅 Ingresado: {fecha_ing if fecha_ing else 'Sin fecha'}")
            ctk.CTkLabel(tarjeta, text=info, font=("Arial", 13), justify="left", text_color="black").pack(side="left", padx=10)
            
            btn_frame = ctk.CTkFrame(tarjeta, fg_color="transparent"); btn_frame.pack(side="right", padx=10)
            
            ctk.CTkButton(btn_frame, text="👁️ Ver Código", fg_color="teal", width=80, command=lambda qr=r_qr, bar=r_barras: self.ver_codigos(qr, bar)).pack(side="left", padx=5)
            
            def check_edit(i_id=id_item):
                if not self.current_user['p_crear_inv'] and self.current_user['rol'] != 'admin':
                    if not pedir_password(self, "Permiso Admin requerido"): return
                self.editar_articulo_completo(i_id)
                
            ctk.CTkButton(btn_frame, text="✏️ Editar", fg_color="orange", width=80, command=check_edit).pack(side="left", padx=5)
            
            def check_delete(i_id=id_item, n=nombre, p=precio, s=stock, img=img_ruta):
                if not self.current_user['p_elim_inv'] and self.current_user['rol'] != 'admin':
                    if not pedir_password(self, "Permiso Admin requerido"): return
                else:
                    if not pedir_password(self, "Confirma tu clave Admin"): return 
                self.seguridad_eliminar(i_id, n, p, s, img)
                
            ctk.CTkButton(btn_frame, text="🗑️ Eliminar", fg_color="red", width=80, command=check_delete).pack(side="left", padx=5)
        
        if termino: self.entry_buscar.delete(0, 'end')

    def ver_codigos(self, r_qr, r_barras):
        win = ctk.CTkToplevel(self); win.title("Códigos del Artículo"); win.geometry("350x550"); win.attributes("-topmost", True)
        scroll = ctk.CTkScrollableFrame(win); scroll.pack(fill="both", expand=True)
        
        def extraer_y_copiar(ruta_boveda):
            data = read_from_vault(ruta_boveda)
            if data:
                temp_dir = os.path.join(os.environ['TEMP'], 'fuxxia_temp')
                os.makedirs(temp_dir, exist_ok=True)
                temp_path = os.path.join(temp_dir, os.path.basename(ruta_boveda))
                with open(temp_path, 'wb') as f: f.write(data)
                copiar_img_portapapeles(temp_path)

        if r_qr:
            try:
                b_data = read_from_vault(r_qr)
                if b_data:
                    img = Image.open(io.BytesIO(b_data)); img.load()
                    ctk_img = ctk.CTkImage(img, size=(150,150))
                    ctk.CTkLabel(scroll, image=ctk_img, text="").pack(pady=10)
                    ctk.CTkButton(scroll, text="📋 Copiar QR", fg_color="teal", command=lambda: extraer_y_copiar(r_qr)).pack(pady=5)
            except: pass
        if r_barras:
            try:
                b_data2 = read_from_vault(r_barras)
                if b_data2:
                    img2 = Image.open(io.BytesIO(b_data2)); img2.load()
                    ctk_img2 = ctk.CTkImage(img2, size=(240,110))
                    ctk.CTkLabel(scroll, image=ctk_img2, text="").pack(pady=10)
                    ctk.CTkButton(scroll, text="📋 Copiar Barras", fg_color="teal", command=lambda: extraer_y_copiar(r_barras)).pack(pady=5)
            except: pass
        def prnt():
            extract_to_temp_and_open(r_qr); extract_to_temp_and_open(r_barras)
        ctk.CTkButton(scroll, text="🖨️ Imprimir", fg_color="blue", command=prnt).pack(pady=20)

    def mover_articulo(self, id_item, ids_orden, pos_actual, direccion):
        nueva_pos = pos_actual + direccion
        if nueva_pos < 0 or nueva_pos >= len(ids_orden): return
        id_otro = ids_orden[nueva_pos]
        cursor = self.conexion.cursor()
        orden_a = cursor.execute("SELECT orden FROM inventario WHERE id=?", (id_item,)).fetchone()[0]
        orden_b = cursor.execute("SELECT orden FROM inventario WHERE id=?", (id_otro,)).fetchone()[0]
        cursor.execute("UPDATE inventario SET orden=? WHERE id=?", (orden_b, id_item))
        cursor.execute("UPDATE inventario SET orden=? WHERE id=?", (orden_a, id_otro))
        self.conexion.commit(); self.buscar_articulos()

    def editar_articulo_completo(self, id_item):
        cursor = self.conexion.cursor()
        cursor.execute("SELECT nombre, variante, stock, precio, costo_codigo, fecha_ingreso, ruta_qr, ruta_barras FROM inventario WHERE id=?", (id_item,))
        row = cursor.fetchone()
        if not row: return
        nombre_act, variante_act, stock_actual, precio_actual, costo_cod, fecha_ing, r_qr_old, r_bar_old = row

        dialog = ctk.CTkToplevel(self); dialog.title(f"Editar ID: {id_item}"); dialog.geometry("400x680"); dialog.attributes("-topmost", True)
        ctk.CTkLabel(dialog, text=f"✏️ Editar ID: {id_item}", font=("Arial", 15, "bold")).pack(pady=10)
        
        form_frame = ctk.CTkFrame(dialog, fg_color="transparent"); form_frame.pack(fill="x", padx=20)
        ctk.CTkLabel(form_frame, text="Nombre:").pack(anchor="w")
        entry_nombre = ctk.CTkEntry(form_frame, width=340); entry_nombre.insert(0, nombre_act or ""); entry_nombre.pack(pady=3)
        ctk.CTkLabel(form_frame, text="Variante:").pack(anchor="w")
        entry_variante = ctk.CTkEntry(form_frame, width=340); entry_variante.insert(0, variante_act or ""); entry_variante.pack(pady=3)
        ctk.CTkLabel(form_frame, text="Precio de Venta:").pack(anchor="w")
        entry_precio = ctk.CTkEntry(form_frame, width=340); entry_precio.insert(0, str(precio_actual)); entry_precio.pack(pady=3)
        ctk.CTkLabel(form_frame, text="Fecha de Ingreso:").pack(anchor="w")
        entry_fecha = ctk.CTkEntry(form_frame, width=340); entry_fecha.insert(0, fecha_ing or ""); entry_fecha.pack(pady=3)

        sep_frame = ctk.CTkFrame(dialog, fg_color="#fff3cd", corner_radius=8); sep_frame.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(sep_frame, text="🔒 Campos protegidos", font=("Arial", 11, "italic"), text_color="#856404").pack(pady=4)

        def fila_protegida(label_text, def_val):
            ctk.CTkLabel(sep_frame, text=label_text).pack(anchor="w", padx=10)
            f = ctk.CTkFrame(sep_frame, fg_color="transparent"); f.pack(fill="x", padx=10, pady=2)
            ent = ctk.CTkEntry(f, width=240, state="disabled"); ent.insert(0, str(def_val)); ent.pack(side="left")
            btn = ctk.CTkButton(f, text="🔓", width=40, fg_color="#856404", command=lambda e=ent, b=None: desbloquear(e, b_ref[0]))
            btn.pack(side="left", padx=6)
            b_ref = [btn]; return ent

        def desbloquear(ent, btn):
            if self.current_user['rol'] == 'admin' or pedir_password(dialog, "Permiso Admin"):
                ent.configure(state="normal"); btn.configure(text="✅", fg_color="green", state="disabled")

        entry_id = fila_protegida("ID:", id_item)
        entry_stock = fila_protegida("Stock:", stock_actual)
        costo_legible = decodificar_costo(costo_cod) if costo_cod else ""
        entry_costo = fila_protegida("Código:", costo_legible)

        lbl_error_modal = ctk.CTkLabel(dialog, text="", text_color="red"); lbl_error_modal.pack()

        ruta_nueva_img = [None]
        def select_img():
            r = filedialog.askopenfilename(filetypes=[("Imágenes","*.jpg *.png *.jpeg")]); dialog.attributes("-topmost", True)
            if r: ruta_nueva_img[0] = r; btn_img.configure(text="✅ Imagen lista", fg_color="green")
        btn_img = ctk.CTkButton(dialog, text="📸 Cambiar Foto", command=select_img, width=340); btn_img.pack(pady=4)

        def guardar_cambios():
            nuevo_nombre, nueva_variante = entry_nombre.get().strip() or nombre_act, entry_variante.get().strip() or variante_act
            try: precio = float(entry_precio.get())
            except: precio = precio_actual
            fecha_nueva = entry_fecha.get().strip() or fecha_ing
            try: stock = int(entry_stock.get())
            except: stock = stock_actual
            try: nuevo_id = int(entry_id.get().strip())
            except: nuevo_id = id_item

            costo_nuevo, raw_costo = costo_cod, entry_costo.get().strip()
            if raw_costo and raw_costo != costo_legible: costo_nuevo = codificar_pandetrigo(formatear_costo_logica(raw_costo))

            cursor2 = self.conexion.cursor()
            try:
                nom_l = f"ID{nuevo_id}_{nuevo_nombre.replace(' ','_')}"
                ruta_qr_nueva = f"codigos_qr/{nom_l}_QR.png"
                qr = qrcode.QRCode(version=1, box_size=6, border=3)
                qr.add_data(f"Articulo: {nuevo_nombre}\nVar: {nueva_variante}\nPrecio: {fmt_precio(precio)}")
                qr.make(fit=True); buf_qr = io.BytesIO(); qr.make_image(fill_color="black", back_color="white").save(buf_qr, format='PNG')
                save_to_vault(ruta_qr_nueva, buf_qr.getvalue())

                texto_visual = f"{fmt_precio(precio)} - {costo_nuevo}"
                ruta_barras_nueva = generar_codigo_barras(nuevo_id, texto_visual, f"codigos_barras/{nom_l}_BARRAS")

                cursor2.execute("""UPDATE inventario SET id=?, nombre=?, variante=?, stock=?, precio=?, costo_codigo=?, fecha_ingreso=?, ruta_qr=?, ruta_barras=? WHERE id=?""", 
                                (nuevo_id, nuevo_nombre, nueva_variante, stock, precio, costo_nuevo, fecha_nueva, ruta_qr_nueva, ruta_barras_nueva, id_item))
                
                if ruta_nueva_img[0]:
                    nueva_ruta = f"imagenes_productos/act_{nuevo_id}{os.path.splitext(ruta_nueva_img[0])[1]}"
                    with open(ruta_nueva_img[0], 'rb') as fr: save_to_vault(nueva_ruta, fr.read())
                    cursor2.execute("UPDATE inventario SET ruta_imagen=? WHERE id=?", (nueva_ruta, nuevo_id))

                self.conexion.commit(); self.buscar_articulos(); dialog.destroy()
            except sqlite3.IntegrityError:
                lbl_error_modal.configure(text="❌ Ese ID ya está en uso.")

        ctk.CTkButton(dialog, text="💾 Guardar Cambios", fg_color="blue", width=340, command=guardar_cambios).pack(pady=10)

    # ==========================================
    # INGRESO DE ARTÍCULOS
    # ==========================================
    def construir_pestaña_ingreso(self):
        if not self.current_user['p_crear_inv'] and self.current_user['rol'] != 'admin':
            return
            
        frame_izq = ctk.CTkFrame(self.tab_ingreso, fg_color="transparent")
        frame_izq.pack(side="left", fill="both", expand=True, padx=20, pady=20)
        self.frame_der = ctk.CTkFrame(self.tab_ingreso, width=380, fg_color="white", corner_radius=10)
        self.frame_der.pack(side="right", fill="y", padx=20, pady=20)

        self.entry_nombre   = ctk.CTkEntry(frame_izq, placeholder_text="Nombre del artículo", width=350); self.entry_nombre.pack(pady=8)
        self.entry_variante = ctk.CTkEntry(frame_izq, placeholder_text="Variante (color, talla...)", width=350); self.entry_variante.pack(pady=8)
        self.entry_costo    = ctk.CTkEntry(frame_izq, placeholder_text="Código", width=350); self.entry_costo.pack(pady=8)
        self.entry_precio   = ctk.CTkEntry(frame_izq, placeholder_text="Precio de Venta", width=350); self.entry_precio.pack(pady=8)
        self.entry_stock    = ctk.CTkEntry(frame_izq, placeholder_text="Stock Inicial", width=350); self.entry_stock.pack(pady=8)

        self.lbl_preview_img = ctk.CTkLabel(frame_izq, text="[Vista Previa de Foto]", width=120, height=120, fg_color="lightgray", corner_radius=10); self.lbl_preview_img.pack(pady=10)
        ctk.CTkButton(frame_izq, text="📸 Seleccionar Foto", fg_color="gray", command=self.seleccionar_imagen).pack(pady=5)
        ctk.CTkButton(frame_izq, text="Guardar Artículo y Generar Etiquetas", font=("Arial", 16, "bold"), fg_color="green", command=self.guardar_articulo).pack(pady=20)
        self.label_mensaje = ctk.CTkLabel(frame_izq, text=""); self.label_mensaje.pack(pady=5)

        ctk.CTkLabel(self.frame_der, text="Etiquetas Generadas", font=("Arial", 18, "bold"), text_color="black").pack(pady=10)
        
        frame_qr = ctk.CTkFrame(self.frame_der, fg_color="transparent"); frame_qr.pack(pady=5)
        self.lbl_qr_img = ctk.CTkLabel(frame_qr, text="Aún no hay QR", width=160, height=160, fg_color="#e8e8e8", corner_radius=8, text_color="gray"); self.lbl_qr_img.pack(side="left", padx=5)

        frame_barras = ctk.CTkFrame(self.frame_der, fg_color="transparent"); frame_barras.pack(pady=10)
        self.lbl_barras_img = ctk.CTkLabel(frame_barras, text="Aún no hay código", width=220, height=110, fg_color="#e8e8e8", corner_radius=8, text_color="gray"); self.lbl_barras_img.pack(side="top", pady=5)

        self.btn_imprimir_etiqueta = ctk.CTkButton(self.frame_der, text="🖨️ Imprimir Ambas", state="disabled", command=self.imprimir_etiquetas); self.btn_imprimir_etiqueta.pack(pady=15)
        self.rutas_imprimir = []

    def seleccionar_imagen(self):
        ruta = filedialog.askopenfilename(title="Seleccionar imagen", filetypes=[("Imágenes","*.jpg *.png *.jpeg")])
        if ruta:
            self.ruta_imagen_seleccionada = ruta
            try:
                with open(ruta,'rb') as f: img_pil = Image.open(io.BytesIO(f.read())); img_pil.load()
                img_ctk = ctk.CTkImage(light_image=img_pil.copy(), size=(120,120))
                self.lbl_preview_img.configure(image=img_ctk, text="", fg_color="transparent"); self.lbl_preview_img.image = img_ctk
            except: self.lbl_preview_img.configure(text="Error visualizando", image=None)

    def guardar_articulo(self):
        nombre, variante, costo_num, precio_str, stock_str = self.entry_nombre.get().strip(), self.entry_variante.get().strip(), self.entry_costo.get().strip(), self.entry_precio.get().strip(), self.entry_stock.get().strip()
        if not nombre or not precio_str: self.label_mensaje.configure(text="❌ Nombre y Precio son obligatorios", text_color="red"); return
        try: precio = float(precio_str); stock = int(stock_str) if stock_str else 0
        except: self.label_mensaje.configure(text="❌ Precio y Stock deben ser números", text_color="red"); return

        costo_cod = codificar_pandetrigo(formatear_costo_logica(costo_num)) if costo_num else ""
        fecha_ing = datetime.now().strftime("%d/%m/%Y %H:%M")

        cursor = self.conexion.cursor()
        cursor.execute("SELECT COALESCE(MAX(orden),0)+1 FROM inventario")
        nuevo_orden = cursor.fetchone()[0]
        cursor.execute('''INSERT INTO inventario (nombre, variante, costo_codigo, fecha_codigo, precio, stock, fecha_ingreso, orden) VALUES (?,?,?,?,?,?,?,?)''', (nombre, variante, costo_cod, obtener_fecha_codificada(), precio, stock, fecha_ing, nuevo_orden))
        id_nuevo = cursor.lastrowid; self.conexion.commit()

        nom_l = f"ID{id_nuevo}_{nombre.replace(' ','_')}"
        try:
            ruta_img = "Sin imagen"
            if self.ruta_imagen_seleccionada and os.path.exists(self.ruta_imagen_seleccionada):
                ext = os.path.splitext(self.ruta_imagen_seleccionada)[1]
                ruta_img = f"imagenes_productos/{nom_l}{ext}"
                with open(self.ruta_imagen_seleccionada, 'rb') as fr: save_to_vault(ruta_img, fr.read())

            ruta_qr = f"codigos_qr/{nom_l}_QR.png"
            qr = qrcode.QRCode(version=1, box_size=6, border=3)
            qr.add_data(f"Articulo: {nombre}\nVar: {variante}\nPrecio: {fmt_precio(precio)}")
            qr.make(fit=True); buf_qr = io.BytesIO(); qr.make_image(fill_color="black", back_color="white").save(buf_qr, format='PNG')
            save_to_vault(ruta_qr, buf_qr.getvalue())

            texto_visual = f"{fmt_precio(precio)} - {costo_cod}"
            ruta_barras = generar_codigo_barras(id_nuevo, texto_visual, f"codigos_barras/{nom_l}_BARRAS")
            
            cursor.execute("UPDATE inventario SET ruta_imagen=?, ruta_qr=?, ruta_barras=? WHERE id=?", (ruta_img, ruta_qr, ruta_barras, id_nuevo)); self.conexion.commit()

            img_qr = Image.open(io.BytesIO(read_from_vault(ruta_qr))); img_qr.load()
            self.img_qr_ctk = ctk.CTkImage(light_image=img_qr.copy(), size=(160,160))
            self.lbl_qr_img.configure(image=self.img_qr_ctk, text="", fg_color="transparent"); self.lbl_qr_img.image = self.img_qr_ctk

            img_bar = Image.open(io.BytesIO(read_from_vault(ruta_barras))); img_bar.load()
            self.img_barras_ctk = ctk.CTkImage(light_image=img_bar.copy(), size=(260, 110))
            self.lbl_barras_img.configure(image=self.img_barras_ctk, text="", fg_color="transparent"); self.lbl_barras_img.image = self.img_barras_ctk

            self.frame_der.update_idletasks()
            self.btn_imprimir_etiqueta.configure(state="normal")
            self.rutas_imprimir = [ruta_qr, ruta_barras]
            
            self.label_mensaje.configure(text=f"✅ ¡Éxito! ID: {id_nuevo}", text_color="green")
            for e in [self.entry_nombre, self.entry_variante, self.entry_costo, self.entry_precio, self.entry_stock]: e.delete(0,'end')
            self.ruta_imagen_seleccionada = None; self.lbl_preview_img.configure(image=None, text="[Vista Previa de Foto]", fg_color="lightgray")
            
            if hasattr(self, 'entry_buscar'): self.entry_buscar.delete(0, 'end'); self.buscar_articulos()
        except Exception as e: self.label_mensaje.configure(text=f"❌ Error: {str(e)}", text_color="red")

    def imprimir_etiquetas(self):
        try:
            for ruta in self.rutas_imprimir: extract_to_temp_and_open(ruta)
        except: pass

    # ==========================================
    # REPORTES Y VENTAS
    # ==========================================
    def construir_pestaña_reportes(self):
        frame_top = ctk.CTkFrame(self.tab_reportes)
        frame_top.pack(fill="x", pady=6, padx=10)
        ctk.CTkLabel(frame_top, text="Reportes y Ventas", font=("Arial", 17, "bold")).pack(side="left", padx=10)
        ctk.CTkButton(frame_top, text="🔄 Refrescar", fg_color="teal", width=80, command=lambda: [self.cargar_ventas(), self.cargar_gastos()]).pack(side="right", padx=4)
        ctk.CTkButton(frame_top, text="📥 Exportar CSV", fg_color="green", width=100, command=self.ventana_exportar).pack(side="right", padx=4)
        ctk.CTkButton(frame_top, text="💸 Ingresar Gasto", fg_color="#cc0000", width=110, command=self.ventana_ingresar_gasto).pack(side="right", padx=4)
        ctk.CTkButton(frame_top, text="🗑️ Historial Elim.", fg_color="#555555", width=110, command=self.ventana_historial_eliminaciones).pack(side="right", padx=4)

        frame_busca = ctk.CTkFrame(self.tab_reportes, fg_color="transparent"); frame_busca.pack(fill="x", padx=10, pady=2)
        self.entry_busca_factura = ctk.CTkEntry(frame_busca, placeholder_text="🔍 Buscar factura por cliente, CC, ID...", font=("Arial", 13))
        self.entry_busca_factura.pack(side="left", fill="x", expand=True, padx=(0,6))
        self.entry_busca_factura.bind("<Button-1>", lambda e: self.entry_busca_factura.delete(0, 'end'))
        self.entry_busca_factura.bind("<FocusIn>", lambda e: self.entry_busca_factura.delete(0, 'end'))
        self.entry_busca_factura.bind("<Return>", lambda e: self.cargar_ventas())
        ctk.CTkButton(frame_busca, text="Buscar", width=80, command=self.cargar_ventas).pack(side="left")

        panel = ctk.CTkFrame(self.tab_reportes, fg_color="transparent"); panel.pack(fill="both", expand=True, padx=10, pady=4)
        frame_ventas = ctk.CTkFrame(panel, fg_color="transparent"); frame_ventas.pack(side="left", fill="both", expand=True, padx=(0,5))
        ctk.CTkLabel(frame_ventas, text="🧾 Historial de Ventas", font=("Arial", 13, "bold")).pack(pady=4)
        self.frame_lista_ventas = ctk.CTkScrollableFrame(frame_ventas); self.frame_lista_ventas.pack(fill="both", expand=True)

        frame_gastos = ctk.CTkFrame(panel, width=280, fg_color="#fff5f5", corner_radius=10); frame_gastos.pack(side="right", fill="y", padx=(5,0)); frame_gastos.pack_propagate(False)
        ctk.CTkLabel(frame_gastos, text="💸 Gastos Hoy", font=("Arial", 13, "bold"), text_color="#cc0000").pack(pady=8)
        self.frame_lista_gastos = ctk.CTkScrollableFrame(frame_gastos, width=260); self.frame_lista_gastos.pack(fill="both", expand=True, padx=8, pady=4)

        frame_bottom = ctk.CTkFrame(self.tab_reportes, fg_color="#f0f0f0", corner_radius=8); frame_bottom.pack(fill="x", padx=10, pady=6)
        
        ctk.CTkLabel(frame_bottom, text="Resumen:", font=("Arial", 12, "bold")).grid(row=0, column=0, padx=10, pady=5)
        ctk.CTkButton(frame_bottom, text="Día", fg_color="#2255aa", width=80, command=lambda: self.ventana_resumen("dia")).grid(row=0, column=1, padx=2, pady=5)
        ctk.CTkButton(frame_bottom, text="Mes", fg_color="#335599", width=80, command=lambda: self.ventana_resumen("mes")).grid(row=0, column=2, padx=2, pady=5)
        ctk.CTkButton(frame_bottom, text="Año", fg_color="#446688", width=80, command=lambda: self.ventana_resumen("año")).grid(row=0, column=3, padx=2, pady=5)
        ctk.CTkButton(frame_bottom, text="📅 Específico", fg_color="#557799", width=95, command=lambda: self.abrir_selector_fecha("resumen")).grid(row=0, column=4, padx=5, pady=5)
        
        ctk.CTkLabel(frame_bottom, text="Utilidad 🔒:", font=("Arial", 12, "bold")).grid(row=1, column=0, padx=10, pady=5)
        ctk.CTkButton(frame_bottom, text="Día", fg_color="#4a0e8f", width=80, command=lambda: self.ventana_utilidad_neta("dia")).grid(row=1, column=1, padx=2, pady=5)
        ctk.CTkButton(frame_bottom, text="Mes", fg_color="#4a0e8f", width=80, command=lambda: self.ventana_utilidad_neta("mes")).grid(row=1, column=2, padx=2, pady=5)
        ctk.CTkButton(frame_bottom, text="Año", fg_color="#4a0e8f", width=80, command=lambda: self.ventana_utilidad_neta("año")).grid(row=1, column=3, padx=2, pady=5)
        ctk.CTkButton(frame_bottom, text="📅 Específico", fg_color="#5a1e9f", width=95, command=lambda: self.abrir_selector_fecha("utilidad")).grid(row=1, column=4, padx=5, pady=5)
        
        if self.current_user['p_ver_macros'] or self.current_user['rol'] == 'admin':
            ctk.CTkButton(frame_bottom, text="📊 MACROS Y REPORTES", fg_color="#ff8800", text_color="black", font=("Arial", 14, "bold"), height=50, command=self.ventana_macros).grid(row=0, column=5, rowspan=2, padx=20)

        self.cargar_ventas(); self.cargar_gastos()

    def abrir_selector_fecha(self, tipo):
        if tipo == "utilidad" and not pedir_password(self, "Acceso a Utilidad Neta"): return
        win = ctk.CTkToplevel(self); win.title("Seleccionar Fecha"); win.geometry("300x180"); win.attributes("-topmost", True)
        ctk.CTkLabel(win, text="Ingrese la fecha a consultar:", font=("Arial", 14, "bold")).pack(pady=15)
        entry_fecha = ctk.CTkEntry(win, placeholder_text="DD/MM/AAAA (Ej. 09/03/2026)", width=200); entry_fecha.pack(pady=5)
        lbl_err = ctk.CTkLabel(win, text="", text_color="red"); lbl_err.pack()
        
        def procesar():
            fecha_str = entry_fecha.get().strip()
            try:
                datetime.strptime(fecha_str, "%d/%m/%Y")
                win.destroy()
                if tipo == "resumen": self.ventana_resumen("custom", fecha_str)
                else: self.ventana_utilidad_neta("custom", fecha_str)
            except: lbl_err.configure(text="Formato inválido. Usa DD/MM/AAAA")
        ctk.CTkButton(win, text="Generar Reporte", command=procesar).pack(pady=10)

    # --- MACROS ---
    def ventana_macros(self):
        win = ctk.CTkToplevel(self); win.title("📊 Macros y Análisis de Negocio"); win.geometry("1150x800"); win.attributes("-topmost", True)
        
        frame_top = ctk.CTkFrame(win); frame_top.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(frame_top, text="Filtro de Tiempo:", font=("Arial", 14, "bold")).pack(side="left", padx=10)
        cmb_filtro = ctk.CTkComboBox(frame_top, values=["Hoy", "Este Mes", "Este Año", "Histórico"], width=150); cmb_filtro.pack(side="left", padx=10)
        
        tab_macros = ctk.CTkTabview(win); tab_macros.pack(fill="both", expand=True, padx=10, pady=5)
        tab_graficos = tab_macros.add("📈 Gráficos Financieros")
        tab_rotacion = tab_macros.add("📦 Análisis de Rotación")

        frame_canvas = ctk.CTkFrame(tab_graficos, fg_color="white"); frame_canvas.pack(fill="both", expand=True)
        self.fig_macros = None 

        def generar_graficos():
            for widget in frame_canvas.winfo_children(): widget.destroy()
            filtro, now = cmb_filtro.get(), datetime.now()
            if filtro == "Hoy": like_str = now.strftime("%Y-%m-%d") + "%"; d_per = 1
            elif filtro == "Este Mes": like_str = now.strftime("%Y-%m") + "%"; d_per = max(1, now.day)
            elif filtro == "Este Año": like_str = now.strftime("%Y") + "%"; d_per = max(1, now.timetuple().tm_yday)
            else: like_str = "%"; d_per = 365 

            cursor = self.conexion.cursor()
            cursor.execute("SELECT fecha_venta, detalle_articulos, total_cobrado, cliente_transaccion, valor_transaccion FROM ventas WHERE fecha_venta LIKE ?", (like_str,))
            ventas = cursor.fetchall()
            
            if not ventas: ctk.CTkLabel(frame_canvas, text=f"No hay datos para '{filtro}'.", font=("Arial", 16), text_color="gray").pack(pady=50); return

            pagos_dict = defaultdict(float)
            ventas_por_hora = defaultdict(float)
            conteo_ventas = Counter()
            num_facturas = len(ventas)
            total_dinero = 0

            for fecha_str, det, total, trans_tipo, trans_val in ventas:
                total_dinero += total
                hora = fecha_str[11:13] + ":00"
                ventas_por_hora[hora] += total
                if trans_tipo and trans_tipo != "NO":
                    pagos_dict[trans_tipo] += trans_val
                    pagos_dict["EFECTIVO"] += (total - trans_val)
                else: pagos_dict["EFECTIVO"] += total

                arts = [a.split("|")[0].strip() for a in det.split(",")]
                for a in arts: 
                    if a: conteo_ventas[a] += 1

            promedio = total_dinero / num_facturas if num_facturas else 0
            kpi_frame = ctk.CTkFrame(frame_canvas, fg_color="#fffbe6", height=60)
            kpi_frame.pack(fill="x", pady=10, padx=20)
            ctk.CTkLabel(kpi_frame, text=f"👥 Promedio de Compra por Cliente: {fmt_precio(promedio)}", font=("Arial", 18, "bold"), text_color="#aa6600").pack(pady=15)

            plt.rcParams.update({'font.size': 12})
            fig = plt.figure(figsize=(12, 8))
            self.fig_macros = fig
            ax1 = plt.subplot(221) # Torta Arts
            ax2 = plt.subplot(222) # Torta Pagos
            ax3 = plt.subplot(212) # Peak line
            
            def plot_pie_con_lineas(ax, sizes, labels, title):
                wedges, texts = ax.pie(sizes, startangle=90, colors=plt.cm.Set3.colors, wedgeprops=dict(edgecolor='w', linewidth=1.5))
                bbox_props = dict(boxstyle="square,pad=0.3", fc="w", ec="w", lw=0)
                kw = dict(arrowprops=dict(arrowstyle="-", color="gray"), bbox=bbox_props, zorder=0, va="center")
                
                for i, p in enumerate(wedges):
                    ang = (p.theta2 - p.theta1)/2. + p.theta1
                    y = np.sin(np.deg2rad(ang)); x = np.cos(np.deg2rad(ang))
                    horizontalalignment = {-1: "right", 1: "left"}[int(np.sign(x))]
                    connectionstyle = f"angle,angleA=0,angleB={ang}"
                    kw["arrowprops"].update({"connectionstyle": connectionstyle})
                    pct = sizes[i] / sum(sizes) * 100
                    
                    if pct > 4: ax.text(0.6*x, 0.6*y, f"{pct:.1f}%", ha='center', va='center', fontweight='bold', fontsize=10)
                    elif pct > 0: ax.annotate(f"{pct:.1f}%", xy=(x, y), xytext=(1.35*np.sign(x), 1.4*y), horizontalalignment=horizontalalignment, **kw)
                
                legend_labels = [f"{l} ({s:.0f})" for l, s in zip(labels, sizes)] if title == "Top 5 Artículos Más Vendidos" else labels
                ax.legend(wedges, legend_labels, loc="center left", bbox_to_anchor=(1.15, 0.5), fontsize=11)
                ax.set_title(title, fontweight="bold", pad=20)

            top_arts = conteo_ventas.most_common(5)
            if top_arts: plot_pie_con_lineas(ax1, [a[1] for a in top_arts], [a[0][:15] for a in top_arts], "Top 5 Artículos Más Vendidos")

            pagos_dict = {k: v for k, v in pagos_dict.items() if v > 0}
            if pagos_dict: plot_pie_con_lineas(ax2, list(pagos_dict.values()), list(pagos_dict.keys()), "Medios de Pago")
            
            if ventas_por_hora:
                horas_ord = sorted(ventas_por_hora.keys())
                vals_ord = [ventas_por_hora[h] for h in horas_ord]
                ax3.plot(horas_ord, vals_ord, color='teal', marker='o', linewidth=2, markersize=8)
                ax3.fill_between(horas_ord, vals_ord, color='teal', alpha=0.1)
                ax3.set_title("Pico de Ventas por Hora ($)", fontweight="bold")
                ax3.tick_params(axis='x', rotation=45)

            fig.tight_layout(pad=3.0)
            canvas = FigureCanvasTkAgg(fig, master=frame_canvas)
            canvas.draw(); canvas.get_tk_widget().pack(fill="both", expand=True)

            # TAB ROTACION
            for w in tab_rotacion.winfo_children(): w.destroy()
            inv_raw = cursor.execute("SELECT nombre, fecha_ingreso FROM inventario").fetchall()
            fechas_ing = {r[0]: r[1] for r in inv_raw if r[1]}
            
            conteo_total = {name: 0 for name in fechas_ing.keys()}
            for a, c in conteo_ventas.items(): 
                if a in conteo_total: conteo_total[a] += c
                else: conteo_total[a] = c
            
            rotacion_lista = []
            for art_name, qty in conteo_total.items():
                rot_qty = qty / d_per
                f_ing = fechas_ing.get(art_name, "")
                d_store_str = "?"
                if f_ing:
                    try:
                        f_dt = datetime.strptime(f_ing, "%d/%m/%Y %H:%M")
                        d_store = max(1, (now - f_dt).days)
                        d_store_str = f"{d_store} días"
                    except: pass
                rotacion_lista.append({"nombre": art_name, "qty": qty, "rot_dia": rot_qty, "dias_tienda": d_store_str})
            
            self.data_export = rotacion_lista
            rot_ord_fast = sorted(rotacion_lista, key=lambda x: x["rot_dia"], reverse=True)

            f_busq = ctk.CTkFrame(tab_rotacion); f_busq.pack(fill="x", padx=10, pady=5)
            entry_b = ctk.CTkEntry(f_busq, placeholder_text="Buscar rotación por artículo...", width=300)
            entry_b.pack(side="left", padx=10, pady=5)
            entry_b.bind("<Button-1>", lambda e: entry_b.delete(0, 'end'))

            f_list_rot = ctk.CTkScrollableFrame(tab_rotacion); f_list_rot.pack(fill="both", expand=True, padx=10, pady=5)

            def pintar_rot(lista):
                for w in f_list_rot.winfo_children(): w.destroy()
                for d in lista:
                    color = "black" if d['qty'] > 0 else "red"
                    ctk.CTkLabel(f_list_rot, text=f"📦 {d['nombre']}  |  Vendidos: {d['qty']}  |  Rotación: {d['rot_dia']:.1f} und/día  |  En tienda hace: {d['dias_tienda']}", font=("Arial", 14), text_color=color).pack(anchor="w", pady=4)

            def fil_rot(*args):
                term = entry_b.get().lower()
                if not term: 
                    top_5 = [x for x in rot_ord_fast if x['qty'] > 0][:5]
                    bot_5 = [x for x in reversed(rot_ord_fast) if x['qty'] == 0][:5]
                    if not bot_5: bot_5 = rot_ord_fast[-5:]
                    pintar_rot(top_5 + [{"nombre":"--- MENOS ROTACIÓN (Hacen Estorbo) ---","qty":0,"rot_dia":0,"dias_tienda":"-"}] + bot_5)
                else: pintar_rot([x for x in rot_ord_fast if term in x["nombre"].lower()])
            
            entry_b.bind("<KeyRelease>", fil_rot)
            fil_rot() 

        ctk.CTkButton(frame_top, text="Generar Gráficos", fg_color="blue", width=150, command=generar_graficos).pack(side="left", padx=20)
        
        def export_macros():
            if not self.fig_macros: return
            carpeta = filedialog.askdirectory(title="Seleccionar Carpeta para Exportar")
            if carpeta:
                ruta_csv = os.path.join(carpeta, f"Macros_Datos_{cmb_filtro.get()}.csv")
                with open(ruta_csv, 'w', newline='', encoding='utf-8-sig') as f:
                    w = csv.writer(f, delimiter=';')
                    w.writerow(["Articulo", "Unidades Vendidas", "Rotacion x Dia", "Dias en Tienda"])
                    for d in getattr(self, 'data_export', []): w.writerow([d['nombre'], d['qty'], round(d['rot_dia'],2), d['dias_tienda']])
                ruta_img = os.path.join(carpeta, f"Macros_Graficos_{cmb_filtro.get()}.png")
                self.fig_macros.savefig(ruta_img, bbox_inches='tight')
                self._popup("Exportación Exitosa", f"Se guardaron 2 archivos en la carpeta:\n\n1. {os.path.basename(ruta_csv)}\n2. {os.path.basename(ruta_img)}")

        ctk.CTkButton(frame_top, text="📥 Exportar Gráficos y CSV", fg_color="green", command=export_macros).pack(side="right", padx=10)
        generar_graficos() 

    def cargar_ventas(self):
        for w in self.frame_lista_ventas.winfo_children(): w.destroy()
        try:
            filtro = self.entry_busca_factura.get().strip() if hasattr(self, 'entry_busca_factura') else ""
            filtro_like = f"%{filtro}%"
            cursor = self.conexion.cursor()
            if filtro: cursor.execute("""SELECT id, fecha_venta, cliente_nombre, cliente_cc, cliente_transaccion, valor_transaccion, total_cobrado, aplico_iva, detalle_articulos FROM ventas WHERE CAST(id AS TEXT) LIKE ? OR cliente_nombre LIKE ? OR cliente_cc LIKE ? ORDER BY id DESC LIMIT 50""", (filtro_like, filtro_like, filtro_like))
            else: cursor.execute("""SELECT id, fecha_venta, cliente_nombre, cliente_cc, cliente_transaccion, valor_transaccion, total_cobrado, aplico_iva, detalle_articulos FROM ventas ORDER BY id DESC LIMIT 50""")
            ventas = cursor.fetchall()
            if not ventas: ctk.CTkLabel(self.frame_lista_ventas, text="No hay ventas registradas.", font=("Arial",13), text_color="gray").pack(pady=20); return
            for v in ventas:
                id_v, fecha, cliente, cc, trans, val_trans, total, iva, detalle = v
                if trans and trans != "NO": trans_str = f" | {trans}: {fmt_precio(val_trans)}"
                else: trans_str = " | EFE"
                
                clean_detalle = ", ".join([x.split("|")[0].strip() for x in detalle.split(",") if x.strip()])
                
                t = ctk.CTkFrame(self.frame_lista_ventas, fg_color="white", corner_radius=8); t.pack(fill="x", pady=3, padx=4)
                info_frame = ctk.CTkFrame(t, fg_color="transparent"); info_frame.pack(side="left", fill="x", expand=True)
                texto = (f"🧾 #{id_v}  |  {fecha}  |  👤 {cliente}\n💵 {fmt_precio(total)}{trans_str}  |  📦 {clean_detalle[:40]}...")
                ctk.CTkLabel(info_frame, text=texto, font=("Arial",12), text_color="black", justify="left").pack(side="left", padx=10, pady=6)
                btn_frame = ctk.CTkFrame(t, fg_color="transparent"); btn_frame.pack(side="right", padx=6)
                ruta_fact = f"facturas/Factura_{id_v}.txt"
                ctk.CTkButton(btn_frame, text="👁", width=40, height=28, fg_color="#555555", command=lambda r=ruta_fact: self.ver_factura(r)).pack(side="left", padx=2)
                ctk.CTkButton(btn_frame, text="🖨", width=40, height=28, fg_color="#2266bb", command=lambda r=ruta_fact: self.imprimir_factura(r)).pack(side="left", padx=2)
                
                def check_del_fac(i=id_v, c=cliente):
                    if not self.current_user['p_elim_fac'] and self.current_user['rol'] != 'admin':
                        if not pedir_password(self, "Permiso Admin requerido"): return
                    else:
                        if not pedir_password(self, "Confirmar Clave Admin"): return
                    self.eliminar_factura(i, c)
                
                ctk.CTkButton(btn_frame, text="🗑", width=40, height=28, fg_color="#cc2222", command=check_del_fac).pack(side="left", padx=2)
            if filtro: self.entry_busca_factura.delete(0, 'end')
        except Exception as e: ctk.CTkLabel(self.frame_lista_ventas, text=f"Error: {str(e)}", text_color="red").pack(pady=10)

    def ver_factura(self, ruta):
        data = read_from_vault(ruta)
        if not data: self._popup("No encontrada", f"Archivo no existe:\n{ruta}"); return
        win = ctk.CTkToplevel(self); win.title("Ver Factura"); win.geometry("480x520"); win.attributes("-topmost", True)
        txt = ctk.CTkTextbox(win, width=460, height=480, font=("Courier", 12)); txt.pack(padx=10, pady=10); txt.insert("0.0", data.decode('utf-8')); txt.configure(state="disabled")

    def imprimir_factura(self, ruta):
        if not extract_to_temp_and_open(ruta): self._popup("Error", "No se pudo extraer la factura")

    def eliminar_factura(self, id_venta, cliente):
        ruta = f"facturas/Factura_{id_venta}.txt"
        data = read_from_vault(ruta)
        info_extra = data.decode('utf-8') if data else ""
        registrar_eliminacion("Factura", f"Fact. #{id_venta} - Cliente: {cliente}", info_extra, "")
        self.conexion.cursor().execute("DELETE FROM ventas WHERE id=?", (id_venta,))
        self.conexion.commit(); self.cargar_ventas()

    def ventana_exportar(self):
        win = ctk.CTkToplevel(self); win.title("Exportar CSV"); win.geometry("300x180"); win.attributes("-topmost", True)
        ctk.CTkLabel(win, text="Seleccione el periodo:", font=("Arial", 14, "bold")).pack(pady=15)
        cmb_export = ctk.CTkComboBox(win, values=["Hoy", "Este Mes", "Este Año", "Todo"]); cmb_export.pack(pady=10)
        def procesar():
            filtro, now = cmb_export.get(), datetime.now()
            if filtro == "Hoy": like_str = now.strftime("%Y-%m-%d") + "%"
            elif filtro == "Este Mes": like_str = now.strftime("%Y-%m") + "%"
            elif filtro == "Este Año": like_str = now.strftime("%Y") + "%"
            else: like_str = "%"
            try:
                cursor = self.conexion.cursor()
                ventas = [list(r) for r in cursor.execute("SELECT id, fecha_venta, cliente_nombre, cliente_cc, cliente_transaccion, valor_transaccion, total_cobrado, aplico_iva, detalle_articulos FROM ventas WHERE fecha_venta LIKE ?", (like_str,)).fetchall()]
                for row in ventas: 
                    row[5], row[6], row[7] = fmt_numero(row[5]), fmt_numero(row[6]), "SI" if row[7] else "NO"
                    row[8] = ", ".join([x.split("|")[0].strip() for x in row[8].split(",") if x.strip()]) 
                ruta = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")], initialfile=f"Reporte_Ventas_{filtro}.csv")
                if ruta:
                    with open(ruta,'w', newline='', encoding='utf-8-sig') as f:
                        writer = csv.writer(f, delimiter=';'); writer.writerow(["ID","Fecha","Cliente","CC","Medio Pago","Valor Pago E.","Total Venta","IVA","Items"]); writer.writerows(ventas)
            except Exception as e: print(e)
            win.destroy()
        ctk.CTkButton(win, text="Exportar", command=procesar).pack(pady=10)

    def ventana_historial_eliminaciones(self):
        win = ctk.CTkToplevel(self); win.title("🗑️ Historial de Eliminaciones"); win.geometry("500x400"); win.attributes("-topmost", True)
        ctk.CTkLabel(win, text="Historial de Eliminaciones (Solo Lectura)", font=("Arial", 16, "bold")).pack(pady=10)
        scroll = ctk.CTkScrollableFrame(win); scroll.pack(fill="both", expand=True, padx=10, pady=10)
        cursor = self.conexion.cursor()
        filas = cursor.execute("SELECT fecha_eliminacion, tipo, detalle, info_extra, ruta_img FROM historial_eliminaciones ORDER BY id DESC").fetchall()
        for f, t, d, info, img in filas: 
            rf = ctk.CTkFrame(scroll, fg_color="transparent"); rf.pack(fill="x", pady=2)
            ctk.CTkLabel(rf, text=f"[{f}] {t}: {d}", font=("Arial",12,"bold")).pack(side="left")
            ctk.CTkButton(rf, text="👁", width=30, fg_color="#555", command=lambda t_=t, d_=d, i=info, im=img: self.ver_detalle_historial(t_, d_, i, im)).pack(side="right")

    def ver_detalle_historial(self, tipo, detalle, info_extra, ruta_img):
        win = ctk.CTkToplevel(self); win.title(f"Detalle de Eliminación"); win.geometry("500x550"); win.attributes("-topmost", True)
        ctk.CTkLabel(win, text=detalle, font=("Arial", 14, "bold")).pack(pady=10)
        if tipo == "Inventario":
            if ruta_img:
                try:
                    b_data = read_from_vault(ruta_img)
                    if b_data:
                        img = Image.open(io.BytesIO(b_data)); img.load()
                        ctk_img = ctk.CTkImage(img, size=(150,150))
                        ctk.CTkLabel(win, image=ctk_img, text="").pack(pady=10)
                except: pass
            else: ctk.CTkLabel(win, text="[Sin Foto Original]", text_color="gray").pack(pady=10)
            ctk.CTkLabel(win, text=info_extra, font=("Arial", 14)).pack(pady=10)
        elif tipo == "Factura":
            txt = ctk.CTkTextbox(win, width=460, height=400, font=("Courier", 12))
            txt.pack(padx=10, pady=10)
            txt.insert("0.0", info_extra if info_extra else "No hay detalles de factura.")
            txt.configure(state="disabled")
        ctk.CTkButton(win, text="Cerrar", command=win.destroy).pack(pady=10)

    def cargar_gastos(self):
        for w in self.frame_lista_gastos.winfo_children(): w.destroy()
        try:
            hoy = datetime.now().strftime("%d/%m/%Y")
            cursor = self.conexion.cursor(); cursor.execute("SELECT id, fecha, cuestion, valor FROM gastos WHERE fecha LIKE ? ORDER BY id DESC", (f"{hoy}%",))
            gastos = cursor.fetchall()
            total_gastos = 0
            if not gastos: ctk.CTkLabel(self.frame_lista_gastos, text="Sin gastos hoy.", font=("Arial",12), text_color="gray").pack(pady=10)
            else:
                for id_g, fecha, cuestion, valor in gastos:
                    t = ctk.CTkFrame(self.frame_lista_gastos, fg_color="#ffe8e8", corner_radius=6); t.pack(fill="x", pady=2)
                    ctk.CTkLabel(t, text=f"📌 {cuestion}\n{fmt_precio(valor)}  |  {fecha[11:]}", font=("Arial",11), justify="left", text_color="#880000").pack(side="left", padx=8, pady=4)
                    ctk.CTkButton(t, text="🗑", width=30, height=26, fg_color="#cc3333", command=lambda gid=id_g: self.eliminar_gasto(gid)).pack(side="right", padx=4)
                    total_gastos += valor or 0
            ctk.CTkLabel(self.frame_lista_gastos, text=f"Total Gastos Hoy: {fmt_precio(total_gastos)}", font=("Arial",12,"bold"), text_color="#cc0000").pack(pady=6)
        except Exception as e: print(e)

    def eliminar_gasto(self, id_gasto):
        self.conexion.cursor().execute("DELETE FROM gastos WHERE id=?", (id_gasto,)); self.conexion.commit(); self.cargar_gastos()

    def ventana_ingresar_gasto(self):
        win = ctk.CTkToplevel(self); win.title("Registrar Gasto"); win.geometry("340x260"); win.attributes("-topmost", True)
        ctk.CTkLabel(win, text="💸 Registrar Gasto", font=("Arial", 16, "bold")).pack(pady=12); ctk.CTkLabel(win, text="Concepto:").pack()
        entry_cuestion = ctk.CTkEntry(win, placeholder_text="Ej: arriendo...", width=280); entry_cuestion.pack(pady=6)
        ctk.CTkLabel(win, text="Valor ($):").pack()
        entry_valor = ctk.CTkEntry(win, placeholder_text="Ej: 50000", width=280); entry_valor.pack(pady=6)
        lbl_err = ctk.CTkLabel(win, text="", text_color="red"); lbl_err.pack()
        def guardar(*args): 
            cuestion = entry_cuestion.get().strip()
            try: valor = float(entry_valor.get().replace('.','').replace(',','.'))
            except: lbl_err.configure(text="❌ Valor inválido"); return
            if not cuestion: lbl_err.configure(text="❌ Ingresa el concepto"); return
            self.conexion.cursor().execute("INSERT INTO gastos (fecha, cuestion, valor) VALUES (?,?,?)", (datetime.now().strftime("%d/%m/%Y %H:%M"), cuestion, valor))
            self.conexion.commit(); self.cargar_gastos(); win.destroy()
        
        entry_valor.bind("<Return>", guardar); entry_cuestion.bind("<Return>", guardar)
        ctk.CTkButton(win, text="✅ Guardar Gasto", fg_color="#cc0000", width=200, command=guardar).pack(pady=14)

    def ventana_resumen(self, periodo, fecha_custom=None):
        now = datetime.now()
        if periodo == "dia": filtro, titulo, filtro_gastos = now.strftime("%Y-%m-%d"), f"Resumen del día {now.strftime('%d/%m/%Y')}", now.strftime("%d/%m/%Y")
        elif periodo == "mes": filtro, titulo, filtro_gastos = now.strftime("%Y-%m"), f"Resumen del mes {now.strftime('%m/%Y')}", now.strftime("%m/%Y")
        elif periodo == "año": filtro, titulo, filtro_gastos = now.strftime("%Y"), f"Resumen del año {now.strftime('%Y')}", now.strftime("%Y")
        elif periodo == "custom":
            d, m, y = fecha_custom.split('/')
            filtro, titulo, filtro_gastos = f"{y}-{m}-{d}", f"Resumen del día {fecha_custom}", fecha_custom
        
        cursor = self.conexion.cursor()
        ventas_raw = cursor.execute("SELECT total_cobrado, detalle_articulos, cliente_transaccion FROM ventas WHERE fecha_venta LIKE ?", (f"{filtro}%",)).fetchall()
        total_ventas = sum(v[0] for v in ventas_raw)
        
        items_resumen = defaultdict(lambda: {"qty": 0, "total": 0.0})
        for v in ventas_raw:
            trans = v[2] if v[2] and v[2] != "NO" else "EFE"
            for art in v[1].split(','):
                art = art.strip()
                if not art: continue
                if "|" in art:
                    parts = art.split("|"); art_name = parts[0].strip(); precio = float(parts[1]) if len(parts) > 1 else 0.0
                else: 
                    art_name = art
                    row = cursor.execute("SELECT precio FROM inventario WHERE nombre LIKE ? LIMIT 1", (f"%{art_name}%",)).fetchone()
                    precio = row[0] if row else 0.0
                
                key = (art_name, trans)
                items_resumen[key]["qty"] += 1
                items_resumen[key]["total"] += precio

        query_g = f"{filtro_gastos}%" if periodo in ["dia", "custom"] else f"%/{filtro_gastos}%"
        gastos_raw = cursor.execute("SELECT cuestion, valor FROM gastos WHERE fecha LIKE ?", (query_g,)).fetchall()
        total_gastos = sum(g[1] for g in gastos_raw)
        neto = total_ventas - total_gastos

        win = ctk.CTkToplevel(self); win.title(titulo); win.geometry("500x580"); win.attributes("-topmost", True)
        ctk.CTkLabel(win, text=f"📋 {titulo}", font=("Arial", 15, "bold")).pack(pady=10)
        panel = ctk.CTkFrame(win, fg_color="transparent"); panel.pack(fill="x", padx=20, pady=5)
        def tarjeta(parent, emoji, label, valor, color):
            f = ctk.CTkFrame(parent, fg_color=color, corner_radius=10); f.pack(fill="x", pady=4)
            ctk.CTkLabel(f, text=f"{emoji} {label}", font=("Arial",12,"bold"), text_color="white").pack(side="left", padx=12, pady=8)
            ctk.CTkLabel(f, text=fmt_precio(valor), font=("Arial",14,"bold"), text_color="white").pack(side="right", padx=12)
        tarjeta(panel, "🧾", f"Ventas ({len(ventas_raw)} facturas)", total_ventas, "#2255aa")
        tarjeta(panel, "💸", f"Gastos ({len(gastos_raw)} reg.)", total_gastos, "#cc3333")
        tarjeta(panel, "💰", "NETO (Ventas − Gastos)", neto, "#117711" if neto >= 0 else "#cc0000")
        
        ctk.CTkLabel(win, text="Detalle de ventas agrupado:", font=("Arial",12,"bold")).pack(anchor="w", padx=20, pady=(8,2))
        frame_det = ctk.CTkScrollableFrame(win, height=180); frame_det.pack(fill="x", padx=20)
        for (art_name, trans), datos in items_resumen.items(): 
            texto_fila = f"• {art_name} x{datos['qty']} [{trans}]: {fmt_precio(datos['total'])}"
            ctk.CTkLabel(frame_det, text=texto_fila, font=("Arial",11), justify="left").pack(anchor="w")

        def imprimir_resumen():
            contenido = f"{'='*42}\n{'FUXXIA - TIENDA DE DETALLES':^42}\n{'='*42}\n{titulo.upper():^42}\n{'-'*42}\n{'Ventas (' + str(len(ventas_raw)) + ' fact.):':<28} {fmt_precio(total_ventas):>12}\n{'Gastos (' + str(len(gastos_raw)) + ' reg.):':<28} {fmt_precio(total_gastos):>12}\n{'-'*42}\n{'NETO:':<28} {fmt_precio(neto):>12}\n{'='*42}\n\n--- DETALLE DE ARTICULOS ---\n"
            for (art_name, trans), datos in items_resumen.items(): contenido += f"  • {art_name} x{datos['qty']} [{trans}]: {fmt_precio(datos['total'])}\n"
            ruta_tmp = f"facturas/Resumen_{now.strftime('%Y%m%d%H%M%S')}.txt"
            save_to_vault(ruta_tmp, contenido.encode('utf-8')); extract_to_temp_and_open(ruta_tmp)

        ctk.CTkButton(win, text="🖨️ Imprimir Resumen", fg_color="#2266bb", width=200, command=imprimir_resumen).pack(pady=6)

    def ventana_utilidad_neta(self, periodo, fecha_custom=None):
        if periodo != "custom" and not pedir_password(self, "Acceso a Utilidad Neta"): return

        now = datetime.now()
        if periodo == "dia": filtro, filtro_gastos, titulo = now.strftime("%Y-%m-%d"), now.strftime("%d/%m/%Y"), f"Utilidad Neta — {now.strftime('%d/%m/%Y')}"
        elif periodo == "mes": filtro, filtro_gastos, titulo = now.strftime("%Y-%m"), now.strftime("%m/%Y"), f"Utilidad Neta — {now.strftime('%m/%Y')}"
        elif periodo == "año": filtro, filtro_gastos, titulo = now.strftime("%Y"), now.strftime("%Y"), f"Utilidad Neta — {now.strftime('%Y')}"
        elif periodo == "custom":
            d, m, y = fecha_custom.split('/')
            filtro, filtro_gastos, titulo = f"{y}-{m}-{d}", fecha_custom, f"Utilidad Neta — {fecha_custom}"

        cursor = self.conexion.cursor()
        ventas_raw = cursor.execute("""SELECT v.total_cobrado, v.aplico_iva, v.detalle_articulos, v.cliente_transaccion FROM ventas v WHERE fecha_venta LIKE ?""", (f"{filtro}%",)).fetchall()
        total_ventas, total_costo = sum(v[0] for v in ventas_raw), 0
        
        detalles_agrupados = defaultdict(lambda: {"qty": 0, "precio": 0, "costo": 0})

        for total_v, iva_v, detalle_v, trans_v in ventas_raw:
            trans = trans_v if trans_v and trans_v != "NO" else "EFE"
            for art in [a.strip() for a in detalle_v.split(",")]:
                if not art: continue
                if "|" in art:
                    parts = art.split("|")
                    art_name, p_art, c_art = parts[0], float(parts[1]), float(parts[2])
                else: 
                    art_name = art
                    row = cursor.execute("SELECT precio, costo_codigo FROM inventario WHERE nombre LIKE ? LIMIT 1", (f"%{art_name}%",)).fetchone()
                    p_art = row[0] if row else 0; c_art = costo_real(row[1]) if row and row[1] else 0
                
                key = (art_name, trans)
                if key not in detalles_agrupados:
                    detalles_agrupados[key]["precio"] = p_art; detalles_agrupados[key]["costo"] = c_art
                detalles_agrupados[key]["qty"] += 1
                total_costo += c_art

        query_g = f"{filtro_gastos}%" if periodo in ["dia", "custom"] else f"%/{filtro_gastos}%"
        gastos_raw = cursor.execute("SELECT cuestion, valor FROM gastos WHERE fecha LIKE ?", (query_g,)).fetchall()
        total_gastos = sum(g[1] for g in gastos_raw)
        bruto, neto = total_ventas - total_gastos, (total_ventas - total_gastos) - total_costo

        win = ctk.CTkToplevel(self); win.title(titulo); win.geometry("540x650"); win.attributes("-topmost", True)
        ctk.CTkLabel(win, text=f"📊 {titulo}", font=("Arial", 15, "bold")).pack(pady=10)
        panel = ctk.CTkFrame(win, fg_color="transparent"); panel.pack(fill="x", padx=20, pady=4)

        def tarjeta(parent, emoji, label, valor, color):
            f = ctk.CTkFrame(parent, fg_color=color, corner_radius=10); f.pack(fill="x", pady=3)
            ctk.CTkLabel(f, text=f"{emoji} {label}", font=("Arial",12,"bold"), text_color="white").pack(side="left", padx=12, pady=8)
            ctk.CTkLabel(f, text=fmt_precio(valor), font=("Arial",14,"bold"), text_color="white").pack(side="right", padx=12)

        tarjeta(panel, "🧾", f"Ventas totales ({len(ventas_raw)} facturas)", total_ventas, "#2255aa")
        tarjeta(panel, "💸", f"Gastos operativos ({len(gastos_raw)})", total_gastos, "#cc3333")
        tarjeta(panel, "📦", f"Costo de mercancía", total_costo, "#775500")
        tarjeta(panel, "📈", "BRUTO (Ventas − Gastos op.)", bruto, "#1a6e1a" if bruto >= 0 else "#cc0000")
        tarjeta(panel, "💰", "NETO (Bruto − Costo)", neto, "#115511" if neto >= 0 else "#880000")

        ctk.CTkLabel(win, text="Detalle agrupado (precio − costo = utilidad):", font=("Arial",12,"bold")).pack(anchor="w", padx=20, pady=(8,2))
        frame_det = ctk.CTkScrollableFrame(win, height=180); frame_det.pack(fill="x", padx=20)
        
        for (art_name, trans), datos in detalles_agrupados.items():
            tot_p, tot_c = datos["precio"] * datos["qty"], datos["costo"] * datos["qty"]
            u_art = tot_p - tot_c
            color = "#006600" if u_art >= 0 else "#cc0000"
            texto_fila = f"• {art_name} x{datos['qty']} [{trans}]:  {fmt_precio(tot_p)} − {fmt_precio(tot_c)} = {fmt_precio(u_art)}"
            ctk.CTkLabel(frame_det, text=texto_fila, font=("Arial",11), text_color=color, justify="left").pack(anchor="w")

        chk_imprimir_detalles = ctk.CTkCheckBox(win, text="Incluir lista detallada en la impresión")
        chk_imprimir_detalles.pack(pady=10)

        def imprimir_utilidad():
            contenido = f"{'='*42}\n{'FUXXIA - UTILIDAD NETA':^42}\n{'='*42}\n{titulo.upper():^42}\n{'-'*42}\n{'Ventas totales:':<28} {fmt_precio(total_ventas):>12}\n{'Gastos operativos:':<28} {fmt_precio(total_gastos):>12}\n{'Costo de mercancía:':<28} {fmt_precio(total_costo):>12}\n{'-'*42}\n{'UTILIDAD NETA:':<28} {fmt_precio(neto):>12}\n{'='*42}\n"
            if chk_imprimir_detalles.get():
                contenido += "\n--- DETALLE DE ARTICULOS ---\n"
                for (art_name, trans), datos in detalles_agrupados.items(): 
                    tot_p, tot_c = datos["precio"] * datos["qty"], datos["costo"] * datos["qty"]
                    contenido += f"  • {art_name} x{datos['qty']} [{trans}]: {fmt_precio(tot_p)} - {fmt_precio(tot_c)} = {fmt_precio(tot_p - tot_c)}\n"
            ruta_tmp = f"facturas/Utilidad_{now.strftime('%Y%m%d%H%M%S')}.txt"
            save_to_vault(ruta_tmp, contenido.encode('utf-8')); extract_to_temp_and_open(ruta_tmp)

        ctk.CTkButton(win, text="🖨️ Imprimir Utilidad", fg_color="#4a0e8f", width=200, command=imprimir_utilidad).pack(pady=6)

if __name__ == "__main__":
    AppPOS().mainloop()
"""
Prompt 
1. Organizzazione base di foto e immagini con:
   - Struttura anno/mese/giorno
   - Gestione duplicati
   - Gestione nomi file uguali

2. Aggiunta supporto per file RAW digitali:
   - Supporto per vari formati (.cr2, .nef, .arw, ecc.)
   - Estrazione metadata specifici RAW
   - Gestione date EXIF dei RAW

3. Aggiunta supporto per file video:
   - Supporto formati video (.mp4, .mov, .mkv, ecc.)
   - Estrazione metadata video
   - Creazione file info per i video

4. Aggiunta funzionalità di sistema:
   - Gestione log
   - Argomenti da riga di comando
   - Notifiche via ntfy.sh

"""
import os
import shutil
from datetime import datetime
import hashlib
from pathlib import Path
import mimetypes
from PIL import Image
import piexif
import rawpy
from hachoir.parser import createParser
from hachoir.metadata import extractMetadata
import argparse
import logging
import requests
import sys
from typing import Optional

def setup_logging(log_file: Optional[str] = None, verbose: bool = False) -> logging.Logger:
    """Configura il sistema di logging."""
    logger = logging.getLogger('media_organizer')
    logger.setLevel(logging.DEBUG)

    # Formattazione del log
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Handler per console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO if not verbose else logging.DEBUG)
    logger.addHandler(console_handler)

    # Handler per file se specificato
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    return logger

def send_ntfy_notification(topic: str, message: str, priority: int = 3) -> None:
    """Invia una notifica tramite ntfy."""
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode(encoding='utf-8'),
            headers={
                "Priority": str(priority),
                "Tags": "file,organize"
            }
        )
    except Exception as e:
        logging.error(f"Errore nell'invio della notifica: {str(e)}")

def get_raw_extensions():
    """Restituisce un set di estensioni RAW comuni."""
    return {
        # Canon
        '.cr2', '.cr3',
        # Nikon
        '.nef', '.nrw',
        # Sony
        '.arw', '.srf', '.sr2',
        # Fujifilm
        '.raf',
        # Olympus
        '.orf',
        # Panasonic
        '.rw2',
        # Pentax
        '.pef', '.dng',
        # Leica
        '.raw', '.rwl',
        # Phase One
        '.iiq',
        # Hasselblad
        '.3fr', '.fff'
    }

def get_video_extensions():
    """Restituisce un set di estensioni video comuni."""
    return {
        # Formati video comuni
        '.mp4', '.mov', '.avi', '.wmv', '.flv', '.mkv',
        '.m4v', '.mpg', '.mpeg', '.3gp', '.webm', '.mts',
        '.m2ts', '.ts', '.vob', '.ogv', '.dv', '.qt'
    }

def get_video_date(file_path):
    """Estrae la data da un file video usando hachoir."""
    try:
        parser = createParser(file_path)
        if not parser:
            return None
        
        metadata = extractMetadata(parser)
        if not metadata:
            return None

        # Prova diverse date disponibili nei metadata
        for date_key in ['creation_date', 'last_modification', 'date']:
            if hasattr(metadata, date_key):
                date_value = getattr(metadata, date_key)
                if date_value:
                    return date_value
                
        return None
    except:
        return None

def get_file_date(file_path):
    """Estrae la data del file da metadata specifici per il tipo di file."""
    try:
        file_extension = os.path.splitext(file_path)[1].lower()
        
        # Gestione video
        if file_extension in get_video_extensions():
            video_date = get_video_date(file_path)
            if video_date:
                return video_date
        
        # Gestione file RAW
        if file_extension in get_raw_extensions():
            try:
                with rawpy.imread(file_path) as raw:
                    if hasattr(raw, 'raw_metadata'):
                        for tag in ['DateTimeOriginal', 'CreateDate', 'ModifyDate']:
                            if tag in raw.raw_metadata:
                                date_str = str(raw.raw_metadata[tag])
                                try:
                                    return datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
                                except ValueError:
                                    continue
            except:
                pass

        # Gestione immagini standard con EXIF
        if file_extension in ('.jpg', '.jpeg', '.tiff'):
            try:
                img = Image.open(file_path)
                exif_dict = piexif.load(img.info.get('exif', b''))
                if exif_dict.get('0th') and piexif.ImageIFD.DateTime in exif_dict['0th']:
                    date_str = exif_dict['0th'][piexif.ImageIFD.DateTime].decode('utf-8')
                    return datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
            except:
                pass
        
        # Fallback alla data del filesystem
        stat = os.stat(file_path)
        creation_time = stat.st_ctime
        modify_time = stat.st_mtime
        return datetime.fromtimestamp(min(creation_time, modify_time))
    except:
        return datetime.now()

def get_video_info(file_path):
    """Estrae informazioni aggiuntive dai file video."""
    try:
        parser = createParser(file_path)
        if not parser:
            return {}
        
        metadata = extractMetadata(parser)
        if not metadata:
            return {}

        info = {}
        
        # Estrai durata
        if hasattr(metadata, 'duration'):
            info['duration'] = str(metadata.duration)
            
        # Estrai dimensioni
        if hasattr(metadata, 'width') and hasattr(metadata, 'height'):
            info['resolution'] = f"{metadata.width}x{metadata.height}"
            
        # Estrai codec
        if hasattr(metadata, 'mime_type'):
            info['format'] = metadata.mime_type
            
        return info
    except:
        return {}

def get_file_hash(file_path):
    """Calcola l'hash MD5 del file per identificare duplicati."""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def is_media_file(file_path):
    """Verifica se il file è un file multimediale."""
    media_extensions = {
        # Immagini standard
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff',
        # Formati RAW
        *get_raw_extensions(),
        # Video
        *get_video_extensions()
    }
    return file_path.lower().endswith(tuple(media_extensions))

def organize_media(source_dir: str, destination_dir: str, logger: logging.Logger, 
                  ntfy_topic: Optional[str] = None, move: bool = False) -> dict:
    """Organizza i file multimediali in cartelle per data."""
    logger.info(f"Inizio organizzazione media da {source_dir} a {destination_dir}")
    
    # Dizionario per tenere traccia degli hash dei file
    processed_files = {}
    
    # Statistiche
    stats = {
        'total_files': 0,
        'raw_files': 0,
        'video_files': 0,
        'image_files': 0,
        'duplicates': 0,
        'errors': 0,
        'space_saved': 0  # in bytes
    }
    
    # Crea la directory di destinazione se non esiste
    os.makedirs(destination_dir, exist_ok=True)
    logger.debug(f"Directory di destinazione creata/verificata: {destination_dir}")
    
    # Scansiona ricorsivamente la directory sorgente
    for root, _, files in os.walk(source_dir):
        for filename in files:
            try:
                if not is_media_file(filename):
                    continue
                    
                source_path = os.path.join(root, filename)
                stats['total_files'] += 1
                logger.debug(f"Processando file: {source_path}")
                
                # Categorizza il file
                extension = os.path.splitext(filename)[1].lower()
                if extension in get_raw_extensions():
                    stats['raw_files'] += 1
                elif extension in get_video_extensions():
                    stats['video_files'] += 1
                else:
                    stats['image_files'] += 1
                
                # Calcola l'hash del file
                file_hash = get_file_hash(source_path)
                
                # Se il file è un duplicato, saltalo
                if file_hash in processed_files:
                    logger.info(f"Duplicato trovato: {filename}")
                    stats['duplicates'] += 1
                    stats['space_saved'] += os.path.getsize(source_path)
                    continue
                
                # Ottieni la data del file
                file_date = get_file_date(source_path)
                
                # Crea il percorso della directory di destinazione
                dest_dir = os.path.join(
                    destination_dir,
                    str(file_date.year),
                    f"{file_date.month:02d}",
                    f"{file_date.day:02d}"
                )
                os.makedirs(dest_dir, exist_ok=True)
                
                # Gestisci i nomi file duplicati
                base_name, extension = os.path.splitext(filename)
                new_filename = filename
                counter = 1
                
                while os.path.exists(os.path.join(dest_dir, new_filename)):
                    new_filename = f"{base_name}_{counter}{extension}"
                    counter += 1
                
                # Copia o sposta il file nella nuova posizione
                destination_path = os.path.join(dest_dir, new_filename)
                if move:
                    logger.debug(f"Spostamento file: {source_path} -> {destination_path}")
                    shutil.move(source_path, destination_path)
                else:
                    logger.debug(f"Copia file: {source_path} -> {destination_path}")
                    shutil.copy2(source_path, destination_path)
                
                # Se è un video, estrai e salva le informazioni
                if extension in get_video_extensions():
                    video_info = get_video_info(source_path)
                    if video_info:
                        info_path = os.path.join(dest_dir, f"{base_name}_info.txt")
                        with open(info_path, 'w') as f:
                            for key, value in video_info.items():
                                f.write(f"{key}: {value}\n")
                
                # Registra l'hash del file
                processed_files[file_hash] = destination_path
                
                logger.info(f"File elaborato con successo: {filename}")
                
            except Exception as e:
                logger.error(f"Errore nel processare {filename}: {str(e)}")
                stats['errors'] += 1
    
    # Log delle statistiche finali
    logger.info("\nStatistiche finali:")
    logger.info(f"File totali processati: {stats['total_files']}")
    logger.info(f"File RAW processati: {stats['raw_files']}")
    logger.info(f"File video processati: {stats['video_files']}")
    logger.info(f"File immagine processati: {stats['image_files']}")
    logger.info(f"Duplicati trovati: {stats['duplicates']}")
    logger.info(f"Spazio risparmiato: {stats['space_saved'] / (1024*1024):.2f} MB")
    logger.info(f"Errori incontrati: {stats['errors']}")
    
    # Invia notifica se richiesto
    if ntfy_topic:
        message = (f"Organizzazione media completata\n"
                  f"Totale: {stats['total_files']}\n"
                  f"RAW: {stats['raw_files']}\n"
                  f"Video: {stats['video_files']}\n"
                  f"Immagini: {stats['image_files']}\n"
                  f"Duplicati: {stats['duplicates']}\n"
                  f"Errori: {stats['errors']}")
        send_ntfy_notification(ntfy_topic, message)
    
    return stats

def main():
    """Funzione principale per l'esecuzione da riga di comando."""
    parser = argparse.ArgumentParser(description="Organizza file multimediali per data")
    parser.add_argument("source", help="Directory sorgente")
    parser.add_argument("destination", help="Directory destinazione")
    parser.add_argument("--move", action="store_true", 
                       help="Sposta i file invece di copiarli")
    parser.add_argument("--log-file", help="File di log (opzionale)")
    parser.add_argument("--verbose", "-v", action="store_true", 
                       help="Mostra messaggi di debug")
    parser.add_argument("--ntfy", help="Topic ntfy per le notifiche (opzionale)")
    
    args = parser.parse_args()
    
    # Setup logging
    logger = setup_logging(args.log_file, args.verbose)
    
    try:
        # Verifica directory
        if not os.path.isdir(args.source):
            logger.error(f"Directory sorgente non valida: {args.source}")
            return 1
            
        # Esegui organizzazione
        stats = organize_media(
            args.source, 
            args.destination, 
            logger,
            args.ntfy,
            args.move
        )
        
        return 0 if stats['errors'] == 0 else 1
        
    except Exception as e:
        logger.error(f"Errore critico: {str(e)}")
        return 1

if __name__ == "__main__":
    sys.exit(main())

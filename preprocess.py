import os
import mne
import json
import argparse
import numpy as np

from scipy.signal import butter, lfilter
from tqdm import tqdm

import src.config as config

mne.set_log_level('ERROR')


def parse():
    
    parser = argparse.ArgumentParser()
    
    parser.add_argument('--split_dir', type=str, default=str("data/splits"))
    parser.add_argument('--input_dir', type=str, default=str("pruned"))
    parser.add_argument('--output_dir', type=str, default=str("preprocessed"))
    parser.add_argument('--split_bands', type=bool, default=False)

    args = parser.parse_args()
    
    # add data_directory
    args.data_dir = config.get_attribute("dataset_path")
    
    return args

class ResidualNan(Exception):
    pass

def interpolate(raw_data):
    
    # replace very large values with nans
    raw_data[abs(raw_data) > 1e2] = np.nan

    # get indices of nans
    nan_indices = np.where(np.isnan(raw_data))
    nan_indices = np.vstack(nan_indices).transpose()

    # hypotesis, Punctual nans
    for channel, timepoint in nan_indices:

        # get value before the point
        before = raw_data[channel, timepoint-1]
        # get value after the point
        after = raw_data[channel, timepoint-1]

        # interpolate
        raw_data[channel, timepoint] = (before + after) / 2

    nan_indices = np.where(np.isnan(raw_data))
    nan_indices = np.vstack(nan_indices).transpose()
    any_nan = nan_indices.shape[0]!=0
    if any_nan:
        raise ResidualNan("Data still contain Nans after interpolation")
        
    return raw_data

def butter_bandpass(lowcut, highcut, fs, order=5):
    return butter(order, [lowcut, highcut], fs=fs, btype='band')

def butter_bandpass_filter(data, lowcut, highcut, fs, order=5):
    b, a = butter_bandpass(lowcut, highcut, fs, order=order)
    y = lfilter(b, a, data)
    return y

def open_and_interpolate(file, split_bands=False):
    CH_NAMES = [
        'Cz', 'Fz', 'Fp1', 'F7', 'F3', 'FC1', 'C3', 'FC5', 'FT9', 'T7', 'CP5', 'CP1', 'P3', 'P7', 'PO9', 'O1', 'Pz', 'Oz', 'O2', 'PO10', 'P8', 'P4', 'CP2', 'CP6', 'T8', 'FT10', 'FC6', 'C4', 'FC2', 'F4', 'F8', 'Fp2'
    ]

    raw_data = mne.io.read_raw_fif(file, preload=True)
    all_ch = raw_data.ch_names
    drop = [ch for ch in all_ch if ch not in CH_NAMES]
    raw_data = raw_data.drop_channels(drop).get_data()

    try:
        interpolated = interpolate(raw_data)
    except ResidualNan as e:
        print(f"Residual NaNs in {file}")
        return None

    fs = 128  # Sampling frequency in Hz (adjust as needed)
    info = mne.create_info(ch_names=CH_NAMES, sfreq=fs, ch_types='eeg')
    interpolated = mne.io.RawArray(interpolated, info)

    interpolated.filter(1., 50.)

    if split_bands:
        interpolated = isolate_bands(interpolated)
    else:
        interpolated = interpolated.get_data()
    # ica = mne.preprocessing.ICA(n_components=20, random_state=15, max_iter=1000, method='picard')
    # ica.fit(interpolated)
    # ica.exclude = [0, 1]
    # interpolated = ica.apply(interpolated).get_data()

    return interpolated

def get_stats(file_list):
    tmp = []
    for file in tqdm(file_list):
        raw_data = open_and_interpolate(file, split_bands=False)
        tmp.append(raw_data)
    # concatenate all the data
    data = np.concatenate(tmp, axis=1)
    #print(data.shape)
    # compute the mean and std
    mean = np.mean(data, axis=1)
    std = np.std(data, axis=1)
    return mean, std

def z_score(raw_data, mean, std):
    return (raw_data - mean[:, np.newaxis]) / std[:, np.newaxis]

def isolate_bands(data):
    FS = 128
    bands = {
        # "delta": (0.5, 4),   
        "theta": (4, 8),     
        "alpha": (8, 12),    
        "beta": (13, 30),   
        "gamma": (30, 60)    
    }
    out = {band_name: None for band_name in bands.keys()}

    for band_name in bands.keys():
        low = bands[band_name][0]
        high = bands[band_name][1]
        band = data.filter(
            low,
            high,
            n_jobs=None,  # use more jobs to speed up.
            l_trans_bandwidth=1,  # make sure filter params are the same
            h_trans_bandwidth=1,
        ) 
        out[band_name] = band.get_data()

    return out


def main(args):
    input_dir = os.path.join(args.data_dir, args.input_dir)
    output_dir = os.path.join(args.data_dir, args.output_dir)

    split_bands = args.split_bands
    
    print(f"Input directory: {input_dir}")
    input_dirs = [os.path.join(input_dir, f) for f in os.listdir(input_dir) if os.path.isdir(os.path.join(input_dir, f))]
    # create the same directory structure in the output directory
    for d in input_dirs:
        print(f"Creating directory {os.path.join(output_dir, os.path.basename(d))}")
        os.makedirs(os.path.join(output_dir, os.path.basename(d)), exist_ok=True)
        
    # Create a list of input files to process
    print("Listing files...")
    files = []
    for dir in input_dirs:
        files.extend([os.path.join(dir, f) for f in os.listdir(dir) if f.endswith(".fif")])
    print(f"Found {len(files)} files to process!")
    
    print("Loading splits...")
    # Load the split file
    split_file_1 = os.path.join(args.split_dir, "splits_subject_identification.json")
    split_file_2 = os.path.join(args.split_dir, "splits_emotion_recognition.json")
    
    splits_1 = json.load(open(split_file_1, 'r'))
    splits_2 = json.load(open(split_file_2, 'r'))
    
    splits = {
        "train": splits_2["train"],
        "val_trial": splits_2["val_trial"],
        "val_subject": splits_2["val_subject"],
        "test_trial": splits_1["test_trial"],
        "test_subject": splits_2["test_subject"]
    }
    
    # Create a list with only train files for statistics
    train_files = [os.path.join(input_dir, "train", f"{s['id']}_eeg.fif") for s in splits["train"]]
    print(f"Found {len(train_files)} train files!")
    
    # Get global train statistics (per channel)
    print("Computing global statistics...")
    mean, std = get_stats(train_files)
    print("Global statistics computed!")
    
    print("Computing subject-wise statistics...")
    # Create a list with only train files for each subject for statistics
    train_files_per_subject = {}
    for file in splits["train"]:
        subject = file["subject_id"]
        if subject not in train_files_per_subject:
            train_files_per_subject[subject] = []
        train_files_per_subject[subject].append(os.path.join(input_dir, "train", f"{file['id']}_eeg.fif"))
    
    # Get train statistics per subject
    stats_per_subject = {
        subject_id : get_stats(files) for subject_id, files in train_files_per_subject.items()
    }
    print("Subject-wise statistics computed!")
    
    print("Preprocessing data...")
    # Process each file
    for file in tqdm(files):
        input_file = file
        output_file = file.replace(".fif", ".npy").replace(input_dir, output_dir)
        
        raw_data = open_and_interpolate(input_file, split_bands)
        if raw_data is None:
            continue

        if split_bands:
            for band_name in raw_data.keys():
                data = raw_data[band_name]
                z_data = z_score(data, mean, std)
                file = output_file.replace(".npy", f"_{band_name}.npy")
                np.save(file, z_data)
        else:
            z_data = z_score(raw_data, mean, std)
            np.save(output_file, z_data)
    
    print("Preprocessing done!")
        
if __name__ == "__main__":
    args = parse()
    main(args)
    
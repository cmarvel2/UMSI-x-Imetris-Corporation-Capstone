import os


def main():
    os.makedirs(r"local_data_lake\incoming\edi_834", exist_ok=True)
    os.makedirs(r"local_data_lake\incoming\edi_835", exist_ok=True)
    os.makedirs(r"local_data_lake\incoming\edi_837", exist_ok=True)

if __name__ == "__main__":
    main()
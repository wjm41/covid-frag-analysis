#! /bin/bash
for i in 0072 0104 0161 0195 0305 0354 0387 0434 0678 0689 0691 0692 0734 0748 0749 0752 0755 0759 0769 0770 0774 0786 0805 0820 0828 0830 0831 0874 0946 0991 1077 1093 1249 1308 1311 1334 1336 1348 1351 1374 1375 1380 1382 1384 1385 1386 1392 1402 1412 1418 1420 1425 1458 1478 1493
do
# babel -imol data/Mpro-x${i}_0.mol -oxyz data/${i}.xyz
python generate_soap.py -xyz xyz/${i}.xyz -tgt npy/${i}.npy
done
python concat_ligands.py

"""Acrescenta ao Sandbox do SARndbox 2.8 os comandos do modo Rosto no cano de controle:
   rosto <foto.png> [giro 0/90/180/270] [espelho 0/1]   foto projetada que se deforma com a areia
   rostoAjuste <ampliação/cm> <deslocamento/cm> <mistura 0-1> <sombra>
   esculpir <rosto.dem>                                    relevo-alvo: vermelho/azul = tirar/pôr areia
   relevo                                                  volta à topografia normal
"""
import sys

def rep(s, old, new):
    if old not in s:
        sys.exit(f"trecho não encontrado: {old[:70]!r}")
    return s.replace(old, new, 1)

h = open("Sandbox.h").read()
if "FaceImage" not in h:
    h = rep(h, "class DEM;\n", "class DEM;\nclass FaceImage;\n") if "class DEM;\n" in h else rep(h, "class Sandbox:public", "class FaceImage;\nclass DEM;\n\nclass Sandbox:public")
    h = rep(h, "\tDEM* activeDem; // The currently active DEM\n", """\tDEM* activeDem; // The currently active DEM
	FaceImage* faceImage; // Modo Rosto: foto projetada (0 = desligado)
	DEM* faceDem; // Modo Esculpir: relevo-alvo do rosto (0 = desligado)
	double faceBoxMin[2],faceBoxMax[2]; // Extensão da caixa de areia em cm (espaço da caixa)
	double faceRefTime; // Última medição da altura média da areia
""")
    h = rep(h, "\t/* Private methods: */\n", """\t/* Private methods: */
	void setFaceMode(FaceImage* newFaceImage,DEM* newFaceDem); // Troca entre relevo, rosto e esculpir
	float measureSandHeight(void) const; // Altura mediana da areia (cm acima do plano base)
""")
    open("Sandbox.h", "w").write(h)

c = open("Sandbox.cpp").read()
if "FaceImage" not in c:
    c = rep(c, '#include "DEMTool.h"\n', '#include "DEMTool.h"\n#include "FaceImage.h"\n')
    c = rep(c, "\t controlPipeFd(-1)", "\t controlPipeFd(-1),\n\t faceImage(0),faceDem(0),faceRefTime(0.0)")
    if "#include <algorithm>" not in c:
        c = rep(c, "#include <vector>\n", "#include <vector>\n#include <algorithm>\n#include <cmath>\n")
    c = rep(c, """	boxSize=Geometry::dist(center,basePlaneCorners[0]);
	for(int i=1;i<4;++i)
		boxSize=Math::max(boxSize,Geometry::dist(center,basePlaneCorners[i]));
""", """	boxSize=Geometry::dist(center,basePlaneCorners[0]);
	for(int i=1;i<4;++i)
		boxSize=Math::max(boxSize,Geometry::dist(center,basePlaneCorners[i]));
	
	/* Modo Rosto: extensão da caixa em cm no espaço da caixa: */
	for(int i=0;i<2;++i)
		{
		faceBoxMin[i]=1.0e30;
		faceBoxMax[i]=-1.0e30;
		}
	for(int i=0;i<4;++i)
		{
		ONTransform::Point bc=boxTransform.transform(basePlaneCorners[i]);
		for(int j=0;j<2;++j)
			{
			faceBoxMin[j]=Math::min(faceBoxMin[j],double(bc[j]));
			faceBoxMax[j]=Math::max(faceBoxMax[j],double(bc[j]));
			}
		}
	
	/* Só a parte da caixa que o projetor ilumina: a caixa calibrada pode ser maior que a imagem
	   projetada. Cada canto da imagem do projetor é levado até o plano base: */
	for(std::vector<RenderSettings>::iterator rsIt=renderSettings.begin();rsIt!=renderSettings.end();++rsIt)
		if(rsIt->fixProjectorView&&rsIt->projectorTransformValid)
			{
			PTransform inv=Geometry::invert(rsIt->projectorTransform);
			double xs[4],ys[4];
			for(int i=0;i<4;++i)
				{
				double nx=(i&1)?1.0:-1.0,ny=(i&2)?1.0:-1.0;
				PTransform::Point p0=inv.transform(PTransform::Point(nx,ny,-1.0));
				PTransform::Point p1=inv.transform(PTransform::Point(nx,ny,1.0));
				double d0=basePlane.calcDistance(p0),d1=basePlane.calcDistance(p1);
				PTransform::Point p=p0+(p1-p0)*(d0/(d0-d1));
				ONTransform::Point bc=boxTransform.transform(p);
				xs[i]=bc[0];
				ys[i]=bc[1];
				}
			for(int i=0;i<4;++i)
				for(int j=i+1;j<4;++j)
					{
					if(xs[j]<xs[i]) std::swap(xs[i],xs[j]);
					if(ys[j]<ys[i]) std::swap(ys[i],ys[j]);
					}
			/* Retângulo de dentro (o quadrilátero iluminado pode estar meio torto): */
			faceBoxMin[0]=Math::max(faceBoxMin[0],xs[1]);
			faceBoxMax[0]=Math::min(faceBoxMax[0],xs[2]);
			faceBoxMin[1]=Math::max(faceBoxMin[1],ys[1]);
			faceBoxMax[1]=Math::min(faceBoxMax[1],ys[2]);
			std::cout<<"Modo Rosto: área iluminada x "<<faceBoxMin[0]<<".."<<faceBoxMax[0]<<" y "<<faceBoxMin[1]<<".."<<faceBoxMax[1]<<" cm"<<std::endl;
			break;
			}
""")
    c = rep(c, """				else
					std::cerr<<"Unrecognized control pipe command "<<tokens[0]<<std::endl;""", """				else if(isToken(tokens[0],"rosto"))
					{
					if(tokens.size()>=2)
						{
						try
							{
							FaceImage* f=new FaceImage(tokens[1].c_str());
							int giro=tokens.size()>=3?atoi(tokens[2].c_str()):0;
							bool espelho=tokens.size()>=4&&atoi(tokens[3].c_str())!=0;
							f->setTransform(OGTransform(boxTransform),faceBoxMin,faceBoxMax,giro,espelho);
							if(faceImage!=0)
								for(int i=0;i<4;++i)
									f->params[i]=faceImage->params[i];
							f->reference=measureSandHeight();
							faceRefTime=Vrui::getApplicationTime();
							setFaceMode(f,0);
							std::cout<<"Modo Rosto: "<<tokens[1]<<" (giro "<<giro<<", espelho "<<espelho<<", areia em "<<f->reference<<" cm)"<<std::endl;
							}
						catch(const std::runtime_error& err)
							{
							std::cerr<<"Não consegui abrir a foto "<<tokens[1]<<": "<<err.what()<<std::endl;
							}
						}
					else
						std::cerr<<"uso: rosto <foto> [giro] [espelho]"<<std::endl;
					}
				else if(isToken(tokens[0],"rostoAjuste"))
					{
					if(tokens.size()==5&&faceImage!=0)
						{
						for(int i=0;i<4;++i)
							faceImage->params[i]=GLfloat(atof(tokens[1+i].c_str()));
						}
					else
						std::cerr<<"uso (no modo Rosto): rostoAjuste <ampliação> <deslocamento> <mistura> <sombra>"<<std::endl;
					}
				else if(isToken(tokens[0],"esculpir"))
					{
					if(tokens.size()==2)
						{
						try
							{
							DEM* d=new DEM;
							d->load(tokens[1].c_str());
							float ref=measureSandHeight();
							d->setTransform(OGTransform::translate(OGTransform::Vector(0,0,-ref))*OGTransform(boxTransform),Scalar(1),Scalar(0));
							setFaceMode(0,d);
							std::cout<<"Modo Esculpir: "<<tokens[1]<<" (areia em "<<ref<<" cm)"<<std::endl;
							}
						catch(const std::runtime_error& err)
							{
							std::cerr<<"Não consegui abrir o relevo "<<tokens[1]<<": "<<err.what()<<std::endl;
							}
						}
					else
						std::cerr<<"uso: esculpir <arquivo.dem>"<<std::endl;
					}
				else if(isToken(tokens[0],"relevo"))
					{
					setFaceMode(0,0);
					std::cout<<"Modo Relevo"<<std::endl;
					}
				else
					std::cerr<<"Unrecognized control pipe command "<<tokens[0]<<std::endl;""")
    # reavalia a altura média da areia de tempos em tempos (o rosto acompanha quem mexe na areia)
    c = rep(c, """	/* Check if the filtered frame has been updated: */
	if(filteredFrames.lockNewValue())
		{
		/* Update the depth image renderer's depth image: */
		depthImageRenderer->setDepthImage(filteredFrames.getLockedValue());
		}
""", """	/* Check if the filtered frame has been updated: */
	if(filteredFrames.lockNewValue())
		{
		/* Update the depth image renderer's depth image: */
		depthImageRenderer->setDepthImage(filteredFrames.getLockedValue());
		
		/* Modo Rosto: acompanha a altura média da areia a cada 3 s */
		if(faceImage!=0&&Vrui::getApplicationTime()-faceRefTime>3.0)
			{
			faceImage->reference=measureSandHeight();
			faceRefTime=Vrui::getApplicationTime();
			}
		}
""")
    c = rep(c, "void Sandbox::toggleDEM(DEM* dem)\n", """float Sandbox::measureSandHeight(void) const
	{
	/* Mediana da altura da areia numa grade espaçada da imagem de profundidade: */
	const Kinect::FrameBuffer& fb=filteredFrames.getLockedValue();
	const float* d=fb.getData<float>();
	if(d==0)
		return 0.0f;
	int w=fb.getSize(0),h=fb.getSize(1);
	PTransform full=PTransform(OGTransform(boxTransform));
	full*=depthImageRenderer->getDepthProjection();
	std::vector<float> hs;
	for(int y=h/10;y<h*9/10;y+=6)
		for(int x=w/10;x<w*9/10;x+=6)
			{
			float z=d[y*w+x];
			if(!std::isfinite(z)||z<=0.0f||z>=2047.0f)
				continue;
			Point p=full.transform(Point(Scalar(x)+Scalar(0.5),Scalar(y)+Scalar(0.5),Scalar(z)));
			if(p[0]<faceBoxMin[0]||p[0]>faceBoxMax[0]||p[1]<faceBoxMin[1]||p[1]>faceBoxMax[1]||std::abs(p[2])>80.0)
				continue;
			hs.push_back(float(p[2]));
			}
	if(hs.empty())
		return 0.0f;
	std::nth_element(hs.begin(),hs.begin()+hs.size()/2,hs.end());
	return hs[hs.size()/2];
	}

void Sandbox::setFaceMode(FaceImage* newFaceImage,DEM* newFaceDem)
	{
	FaceImage* oldFace=faceImage;
	DEM* oldDem=faceDem;
	faceImage=newFaceImage;
	faceDem=newFaceDem;
	
	/* O relevo-alvo do rosto substitui qualquer outro DEM ativo; ao sair, desliga o dele: */
	if(newFaceDem!=0)
		activeDem=newFaceDem;
	else if(activeDem==oldDem)
		activeDem=0;
	
	for(std::vector<RenderSettings>::iterator rsIt=renderSettings.begin();rsIt!=renderSettings.end();++rsIt)
		if(rsIt->fixProjectorView)
			{
			rsIt->surfaceRenderer->setFaceImage(faceImage);
			rsIt->surfaceRenderer->setDem(activeDem);
			}
	
	delete oldFace;
	if(oldDem!=0&&oldDem!=activeDem)
		delete oldDem;
	}

void Sandbox::toggleDEM(DEM* dem)
""")
    open("Sandbox.cpp", "w").write(c)

m = open("makefile").read()
if "FaceImage.cpp" not in m:
    m = rep(m, "                   DEM.cpp \\\n", "                   DEM.cpp \\\n                   FaceImage.cpp \\\n")
    open("makefile", "w").write(m)
print("Sandbox ok")
